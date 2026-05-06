export function buildModelPickerOptions(values, currentValue, missingLabel) {
  const uniqueValues = [...new Set((Array.isArray(values) ? values : []).filter(Boolean))]
  const options = uniqueValues.map(value => ({ value, label: value }))

  if (currentValue && !uniqueValues.includes(currentValue)) {
    options.unshift({
      value: currentValue,
      label: `${currentValue} (${missingLabel})`,
    })
  }

  return options
}
