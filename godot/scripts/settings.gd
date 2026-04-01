## Settings — load/save configuration to user://redclaw_settings.json.
extends Node

const SETTINGS_FILE: String = "user://redclaw_settings.json"

signal settings_loaded(settings: Dictionary)

var _settings: Dictionary = {
	"provider": "openai",
	"model": "",
	"base_url": "",
	"perm_mode": "ask",
	"working_dir": "",
	# Per-provider API keys
	"api_keys": {},
}


func _ready() -> None:
	load_settings()


func get_settings() -> Dictionary:
	return _settings.duplicate()


func get_setting(key: String, default: Variant = null) -> Variant:
	return _settings.get(key, default)


func set_setting(key: String, value: Variant) -> void:
	_settings[key] = value


func set_api_key(provider: String, key: String) -> void:
	var keys: Dictionary = _settings.get("api_keys", {})
	keys[provider] = key
	_settings["api_keys"] = keys
	save_settings()


func get_api_key(provider: String) -> String:
	var keys: Dictionary = _settings.get("api_keys", {})
	return keys.get(provider, "")


func save_settings() -> bool:
	var file: FileAccess = FileAccess.open(SETTINGS_FILE, FileAccess.WRITE)
	if file == null:
		push_warning("Failed to save settings: " + FileAccess.get_open_error())
		return false
	var json_str: String = JSON.stringify(_settings, "\t")
	file.store_string(json_str)
	file.close()
	return true


func load_settings() -> bool:
	if not FileAccess.file_exists(SETTINGS_FILE):
		settings_loaded.emit(_settings)
		return false

	var file: FileAccess = FileAccess.open(SETTINGS_FILE, FileAccess.READ)
	if file == null:
		settings_loaded.emit(_settings)
		return false

	var json_str: String = file.get_as_text()
	file.close()

	var parsed: Variant = JSON.parse_string(json_str)
	if parsed == null:
		push_warning("Failed to parse settings JSON")
		settings_loaded.emit(_settings)
		return false

	var loaded: Dictionary = parsed as Dictionary
	if loaded == null:
		settings_loaded.emit(_settings)
		return false

	# Merge with defaults
	for key in loaded:
		_settings[key] = loaded[key]

	settings_loaded.emit(_settings)
	return true


func reset_settings() -> void:
	_settings = {
		"provider": "openai",
		"model": "",
		"base_url": "",
		"perm_mode": "ask",
		"working_dir": "",
		"api_keys": {},
	}
	save_settings()
