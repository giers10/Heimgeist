#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{collections::HashMap, sync::Mutex};

use serde_json::{json, Value};
use tauri::State;

type SettingsStore = Mutex<HashMap<String, Value>>;

fn default_settings() -> HashMap<String, Value> {
    HashMap::from([
        ("backendApiUrl".into(), json!("http://127.0.0.1:8000")),
        ("ollamaApiUrl".into(), json!("http://127.0.0.1:11434")),
        ("chatModel".into(), json!("llama3")),
        ("visionModel".into(), json!("")),
        ("embedModel".into(), json!("nomic-embed-text:latest")),
        ("rerankModel".into(), json!("nomic-embed-text:latest")),
        ("transcriptionModel".into(), json!("base")),
        ("colorScheme".into(), json!("Default")),
        ("uiScale".into(), json!(1)),
        ("openDevToolsOnStartup".into(), json!(false)),
        ("audioInputEnabled".into(), json!(true)),
        ("audioInputDeviceId".into(), json!("")),
        ("audioInputLanguage".into(), json!("")),
    ])
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

#[tauri::command]
fn get_settings(settings: State<'_, SettingsStore>) -> Result<Value, String> {
    let settings = settings
        .lock()
        .map_err(|_| "Settings store is unavailable.".to_string())?;
    Ok(json!(settings.clone()))
}

#[tauri::command]
fn set_setting(
    key: String,
    value: Value,
    settings: State<'_, SettingsStore>,
) -> Result<bool, String> {
    let mut settings = settings
        .lock()
        .map_err(|_| "Settings store is unavailable.".to_string())?;
    settings.insert(key, value);
    Ok(true)
}

#[tauri::command]
fn update_settings(
    settings: HashMap<String, Value>,
    store: State<'_, SettingsStore>,
) -> Result<bool, String> {
    let mut store = store
        .lock()
        .map_err(|_| "Settings store is unavailable.".to_string())?;
    store.extend(settings);
    Ok(true)
}

#[tauri::command]
fn get_update_status() -> Value {
    unavailable_update_status("Tauri update checks are not implemented in this migration spike.")
}

#[tauri::command]
fn check_for_updates() -> Value {
    unavailable_update_status("Tauri update checks are not implemented in this migration spike.")
}

#[tauri::command]
fn get_changelog_page(page: Option<u64>) -> Value {
    json!({
        "page": page.unwrap_or(1),
        "pageSize": 50,
        "hasMore": false,
        "entries": []
    })
}

#[tauri::command]
fn pick_paths(_options: Option<Value>) -> Vec<String> {
    Vec::new()
}

#[tauri::command]
fn open_path(_file_path: String) -> bool {
    false
}

#[tauri::command]
fn open_external_link(_url: String) -> bool {
    false
}

fn main() {
    tauri::Builder::default()
        .manage(Mutex::new(default_settings()))
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
        .expect("failed to run Heimgeist Tauri migration spike");
}
