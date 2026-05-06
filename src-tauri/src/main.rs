#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    fs,
    path::{Path, PathBuf},
    sync::Mutex,
};

use serde::Deserialize;
use serde_json::{json, Map, Value};
use tauri::{AppHandle, Emitter, Manager, State, WindowEvent};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

const DEFAULT_BACKEND_API_URL: &str = "http://127.0.0.1:8000";
const DEFAULT_OLLAMA_API_URL: &str = "http://127.0.0.1:11434";
const DEFAULT_EMBED_MODEL: &str = "nomic-embed-text:latest";
const DEFAULT_TRANSCRIPTION_MODEL: &str = "base";
const BGE_EMBED_MODEL: &str = "bge-m3:latest";
const DEFAULT_UI_SCALE: f64 = 1.0;
const MIN_UI_SCALE: f64 = 0.7;
const MAX_UI_SCALE: f64 = 1.3;
const SETTINGS_FILE_NAME: &str = "settings.json";
const CHANGELOG_PAGE_SIZE: u64 = 50;

type SettingsMap = Map<String, Value>;

struct SettingsStore {
    path: PathBuf,
    settings: Mutex<SettingsMap>,
}

#[derive(Debug, Deserialize)]
struct PickPathsOptions {
    title: Option<String>,
    filters: Option<Vec<DialogFilter>>,
    multiple: Option<bool>,
}

#[derive(Debug, Deserialize)]
struct DialogFilter {
    name: Option<String>,
    extensions: Option<Vec<String>>,
}

fn default_settings() -> SettingsMap {
    let mut settings = SettingsMap::new();
    settings.insert("backendApiUrl".into(), json!(DEFAULT_BACKEND_API_URL));
    settings.insert("ollamaApiUrl".into(), json!(DEFAULT_OLLAMA_API_URL));
    settings.insert("chatModel".into(), json!("llama3"));
    settings.insert("visionModel".into(), json!(""));
    settings.insert("embedModel".into(), json!(DEFAULT_EMBED_MODEL));
    settings.insert("rerankModel".into(), json!(DEFAULT_EMBED_MODEL));
    settings.insert(
        "transcriptionModel".into(),
        json!(DEFAULT_TRANSCRIPTION_MODEL),
    );
    settings.insert("colorScheme".into(), json!("Default"));
    settings.insert("uiScale".into(), json!(DEFAULT_UI_SCALE));
    settings.insert("openDevToolsOnStartup".into(), json!(false));
    settings.insert("audioInputEnabled".into(), json!(true));
    settings.insert("audioInputDeviceId".into(), json!(""));
    settings.insert("audioInputLanguage".into(), json!(""));
    settings
}

fn value_to_trimmed_string(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(text)) => text.trim().to_string(),
        Some(Value::Number(number)) => number.to_string(),
        Some(Value::Bool(value)) => value.to_string(),
        _ => String::new(),
    }
}

fn value_to_number(value: Option<&Value>) -> Option<f64> {
    match value {
        Some(Value::Number(number)) => number.as_f64(),
        Some(Value::String(text)) => text.trim().parse::<f64>().ok(),
        _ => None,
    }
}

fn normalize_ui_scale(value: Option<&Value>) -> f64 {
    let scale = value_to_number(value).unwrap_or(DEFAULT_UI_SCALE);
    let clamped = scale.clamp(MIN_UI_SCALE, MAX_UI_SCALE);
    (clamped * 100.0).round() / 100.0
}

fn normalize_boolean_setting(value: Option<&Value>) -> bool {
    match value {
        Some(Value::String(text)) => match text.trim().to_lowercase().as_str() {
            "true" | "1" | "yes" | "on" => true,
            "false" | "0" | "no" | "off" | "" => false,
            _ => false,
        },
        Some(Value::Bool(value)) => *value,
        _ => false,
    }
}

fn normalize_model_name(value: Option<&Value>, fallback: &str) -> String {
    let trimmed = value_to_trimmed_string(value);
    if trimmed.is_empty() {
        fallback.to_string()
    } else {
        trimmed
    }
}

fn normalize_embed_model(value: Option<&Value>) -> String {
    let trimmed = value_to_trimmed_string(value);
    if trimmed.is_empty() {
        return DEFAULT_EMBED_MODEL.to_string();
    }

    match trimmed.to_lowercase().as_str() {
        "bge" | "bge-m3" | BGE_EMBED_MODEL => BGE_EMBED_MODEL.to_string(),
        "nomic" | "nomic-embed-text" | DEFAULT_EMBED_MODEL => DEFAULT_EMBED_MODEL.to_string(),
        _ => trimmed,
    }
}

fn looks_like_ollama_url(value: &str) -> bool {
    tauri::Url::parse(value)
        .map(|parsed| {
            parsed.port() == Some(11434)
                || parsed
                    .path()
                    .trim_end_matches('/')
                    .eq_ignore_ascii_case("/api")
        })
        .unwrap_or(false)
}

fn migrate_settings(source: Option<SettingsMap>) -> (SettingsMap, bool) {
    let source = source.unwrap_or_default();
    let mut next = default_settings();
    let mut migrated = false;

    for (key, value) in source.iter() {
        next.insert(key.clone(), value.clone());
    }

    if !source.contains_key("backendApiUrl") {
        if let Some(Value::String(ollama_api_url)) = source.get("ollamaApiUrl") {
            if looks_like_ollama_url(ollama_api_url) {
                next.insert("backendApiUrl".into(), json!(DEFAULT_BACKEND_API_URL));
                next.insert("ollamaApiUrl".into(), json!(ollama_api_url.trim()));
            } else {
                next.insert("backendApiUrl".into(), json!(ollama_api_url.trim()));
                next.insert("ollamaApiUrl".into(), json!(DEFAULT_OLLAMA_API_URL));
            }
            migrated = true;
        }
    }

    if !source.contains_key("openDevToolsOnStartup") {
        migrated = true;
    }

    if !source.contains_key("rerankModel") {
        let embed_model = next
            .get("embedModel")
            .cloned()
            .unwrap_or_else(|| json!(DEFAULT_EMBED_MODEL));
        next.insert("rerankModel".into(), embed_model);
        migrated = true;
    }

    if !source.contains_key("visionModel") {
        let chat_model = value_to_trimmed_string(next.get("chatModel"));
        next.insert("visionModel".into(), json!(chat_model));
        migrated = true;
    }

    if !source.contains_key("transcriptionModel") {
        next.insert(
            "transcriptionModel".into(),
            json!(DEFAULT_TRANSCRIPTION_MODEL),
        );
        migrated = true;
    }

    normalize_settings(&mut next);
    (next, migrated)
}

fn normalize_settings(settings: &mut SettingsMap) {
    let backend_api_url = value_to_trimmed_string(settings.get("backendApiUrl"));
    let ollama_api_url = value_to_trimmed_string(settings.get("ollamaApiUrl"));
    let chat_model = normalize_model_name(settings.get("chatModel"), "");
    let vision_model = normalize_model_name(settings.get("visionModel"), "");
    let embed_model = normalize_embed_model(settings.get("embedModel"));
    let rerank_model = normalize_embed_model(settings.get("rerankModel"));
    let transcription_model = normalize_model_name(
        settings.get("transcriptionModel"),
        DEFAULT_TRANSCRIPTION_MODEL,
    );
    let ui_scale = normalize_ui_scale(settings.get("uiScale"));
    let open_devtools_on_startup = normalize_boolean_setting(settings.get("openDevToolsOnStartup"));
    let audio_input_enabled = normalize_boolean_setting(settings.get("audioInputEnabled"));
    let audio_input_device_id = value_to_trimmed_string(settings.get("audioInputDeviceId"));
    let audio_input_language =
        value_to_trimmed_string(settings.get("audioInputLanguage")).to_lowercase();

    settings.insert("backendApiUrl".into(), json!(backend_api_url));
    settings.insert("ollamaApiUrl".into(), json!(ollama_api_url));
    settings.insert("chatModel".into(), json!(chat_model));
    settings.insert("visionModel".into(), json!(vision_model));
    settings.insert("embedModel".into(), json!(embed_model));
    settings.insert("rerankModel".into(), json!(rerank_model));
    settings.insert("transcriptionModel".into(), json!(transcription_model));
    settings.insert("uiScale".into(), json!(ui_scale));
    settings.insert(
        "openDevToolsOnStartup".into(),
        json!(open_devtools_on_startup),
    );
    settings.insert("audioInputEnabled".into(), json!(audio_input_enabled));
    settings.insert("audioInputDeviceId".into(), json!(audio_input_device_id));
    settings.insert("audioInputLanguage".into(), json!(audio_input_language));
}

fn explicit_settings_file_path() -> Option<PathBuf> {
    if let Ok(path) = std::env::var("HEIMGEIST_SETTINGS_FILE") {
        let trimmed = path.trim();
        if !trimmed.is_empty() {
            return Some(PathBuf::from(trimmed));
        }
    }

    None
}

fn tauri_settings_file_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|dir| dir.join(SETTINGS_FILE_NAME))
        .map_err(|error| format!("Could not resolve Tauri settings path: {error}"))
}

fn settings_file_path(app: &AppHandle) -> Result<PathBuf, String> {
    explicit_settings_file_path()
        .map(Ok)
        .unwrap_or_else(|| tauri_settings_file_path(app))
}

fn paths_equal(first: &Path, second: &Path) -> bool {
    match (first.canonicalize(), second.canonicalize()) {
        (Ok(first), Ok(second)) => first == second,
        _ => first == second,
    }
}

fn push_unique_candidate(candidates: &mut Vec<PathBuf>, candidate: PathBuf, tauri_path: &Path) {
    if paths_equal(&candidate, tauri_path)
        || candidates
            .iter()
            .any(|existing| paths_equal(existing, &candidate))
    {
        return;
    }

    candidates.push(candidate);
}

fn legacy_settings_candidates(app: &AppHandle, tauri_path: &Path) -> Vec<PathBuf> {
    let mut candidates = Vec::new();

    if let Ok(config_dir) = app.path().config_dir() {
        let product_name = app.config().product_name.as_deref().unwrap_or("Heimgeist");

        for app_dir in [product_name, "Heimgeist", "heimgeist"] {
            push_unique_candidate(
                &mut candidates,
                config_dir.join(app_dir).join(SETTINGS_FILE_NAME),
                tauri_path,
            );
        }
    }

    candidates
}

fn import_legacy_settings_if_available(app: &AppHandle, tauri_path: &Path) -> Option<SettingsMap> {
    for candidate in legacy_settings_candidates(app, tauri_path) {
        match read_settings_file(&candidate) {
            Ok(Some(settings)) => {
                println!(
                    "Imported legacy Heimgeist settings for first Tauri run from {}",
                    candidate.display()
                );
                return Some(settings);
            }
            Ok(None) => {}
            Err(error) => {
                eprintln!(
                    "Skipping legacy settings import from {}: {error}",
                    candidate.display()
                );
            }
        }
    }

    None
}

fn read_settings_file(path: &PathBuf) -> Result<Option<SettingsMap>, String> {
    if !path.exists() {
        return Ok(None);
    }

    let data = fs::read_to_string(path)
        .map_err(|error| format!("Could not read settings from {}: {error}", path.display()))?;
    let value: Value = serde_json::from_str(&data)
        .map_err(|error| format!("Could not parse settings from {}: {error}", path.display()))?;

    match value {
        Value::Object(settings) => Ok(Some(settings)),
        _ => Ok(None),
    }
}

fn write_settings_file(path: &PathBuf, settings: &SettingsMap) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| {
            format!(
                "Could not create settings directory {}: {error}",
                parent.display()
            )
        })?;
    }

    let data = serde_json::to_string_pretty(settings)
        .map_err(|error| format!("Could not serialize settings: {error}"))?;
    fs::write(path, format!("{data}\n"))
        .map_err(|error| format!("Could not write settings to {}: {error}", path.display()))
}

fn load_settings_store(app: &AppHandle) -> Result<SettingsStore, String> {
    let path = settings_file_path(app)?;
    let raw_settings = read_settings_file(&path)?;
    let (settings, should_write) = match raw_settings {
        Some(raw_settings) => {
            let (settings, migrated) = migrate_settings(Some(raw_settings));
            (settings, migrated)
        }
        None => {
            let imported_settings = if explicit_settings_file_path().is_none() {
                import_legacy_settings_if_available(app, &path)
            } else {
                None
            };
            let (settings, _migrated) = migrate_settings(imported_settings);
            (settings, true)
        }
    };

    if should_write {
        write_settings_file(&path, &settings)?;
    }

    Ok(SettingsStore {
        path,
        settings: Mutex::new(settings),
    })
}

fn unavailable_update_status(message: &str) -> Value {
    json!({
        "state": "unavailable",
        "message": message,
        "checkedAt": null,
        "localCommit": null,
        "remoteCommit": null,
        "branch": null,
        "restartScheduled": false
    })
}

fn normalize_changelog_page(page: Option<u64>) -> u64 {
    page.filter(|value| *value > 0).unwrap_or(1)
}

#[tauri::command]
fn get_settings(store: State<'_, SettingsStore>) -> Result<Value, String> {
    let settings = store
        .settings
        .lock()
        .map_err(|_| "Settings store is unavailable.".to_string())?;
    Ok(Value::Object(settings.clone()))
}

#[tauri::command]
fn set_setting(key: String, value: Value, store: State<'_, SettingsStore>) -> Result<bool, String> {
    let mut settings = store
        .settings
        .lock()
        .map_err(|_| "Settings store is unavailable.".to_string())?;
    settings.insert(key, value);
    normalize_settings(&mut settings);
    write_settings_file(&store.path, &settings)?;
    Ok(true)
}

#[tauri::command]
fn update_settings(settings: SettingsMap, store: State<'_, SettingsStore>) -> Result<bool, String> {
    let mut current_settings = store
        .settings
        .lock()
        .map_err(|_| "Settings store is unavailable.".to_string())?;
    current_settings.extend(settings);
    normalize_settings(&mut current_settings);
    write_settings_file(&store.path, &current_settings)?;
    Ok(true)
}

#[tauri::command]
fn get_update_status() -> Value {
    unavailable_update_status("Tauri update checks are not implemented in this production release phase.")
}

#[tauri::command]
fn check_for_updates() -> Value {
    unavailable_update_status("Tauri update checks are not implemented in this production release phase.")
}

#[tauri::command]
fn get_changelog_page(page: Option<u64>) -> Value {
    json!({
        "page": normalize_changelog_page(page),
        "pageSize": CHANGELOG_PAGE_SIZE,
        "hasMore": false,
        "entries": []
    })
}

#[tauri::command]
async fn pick_paths(
    app: AppHandle,
    options: Option<PickPathsOptions>,
) -> Result<Vec<String>, String> {
    let options = options.unwrap_or(PickPathsOptions {
        title: None,
        filters: None,
        multiple: None,
    });
    let mut dialog = app.dialog().file();

    if let Some(title) = options
        .title
        .as_deref()
        .map(str::trim)
        .filter(|title| !title.is_empty())
    {
        dialog = dialog.set_title(title);
    }

    for filter in options.filters.unwrap_or_default() {
        let name = filter
            .name
            .as_deref()
            .map(str::trim)
            .filter(|name| !name.is_empty())
            .unwrap_or("Files")
            .to_string();
        let extensions: Vec<String> = filter
            .extensions
            .unwrap_or_default()
            .into_iter()
            .map(|extension| extension.trim().trim_start_matches('.').to_string())
            .filter(|extension| !extension.is_empty())
            .collect();

        if !extensions.is_empty() {
            let extension_refs: Vec<&str> = extensions.iter().map(String::as_str).collect();
            dialog = dialog.add_filter(name, &extension_refs);
        }
    }

    let paths = if options.multiple.unwrap_or(true) {
        dialog.blocking_pick_files().unwrap_or_default()
    } else {
        dialog.blocking_pick_file().into_iter().collect()
    };

    paths
        .into_iter()
        .map(|path| {
            path.into_path()
                .map(|path| path.to_string_lossy().into_owned())
                .map_err(|error| format!("Could not resolve selected file path: {error}"))
        })
        .collect()
}

#[tauri::command]
fn open_path(app: AppHandle, file_path: String) -> Result<bool, String> {
    let file_path = file_path.trim();
    if file_path.is_empty() {
        return Ok(false);
    }

    match app.opener().open_path(file_path, None::<&str>) {
        Ok(_) => Ok(true),
        Err(error) => {
            eprintln!("Failed to open path {file_path}: {error}");
            Ok(false)
        }
    }
}

fn is_allowed_external_url(url: &str) -> bool {
    tauri::Url::parse(url)
        .map(|parsed| matches!(parsed.scheme(), "http" | "https" | "mailto" | "tel"))
        .unwrap_or(false)
}

#[tauri::command]
fn open_external_link(app: AppHandle, url: String) -> Result<bool, String> {
    let url = url.trim();
    if url.is_empty() || !is_allowed_external_url(url) {
        return Ok(false);
    }

    match app.opener().open_url(url, None::<&str>) {
        Ok(_) => Ok(true),
        Err(error) => {
            eprintln!("Failed to open URL {url}: {error}");
            Ok(false)
        }
    }
}

fn main() {
    tauri::Builder::default()
        .menu(|handle| tauri::menu::Menu::default(handle))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let store = load_settings_store(app.handle()).map_err(std::io::Error::other)?;
            app.manage(store);
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, WindowEvent::Focused(true)) {
                let _ = window.emit("window-focused", ());
            }
        })
        .invoke_handler(tauri::generate_handler![
            get_settings,
            set_setting,
            update_settings,
            get_update_status,
            check_for_updates,
            get_changelog_page,
            pick_paths,
            open_path,
            open_external_link
        ])
        .run(tauri::generate_context!())
        .expect("failed to run Heimgeist Tauri desktop shell");
}
