## Session manager — list, switch, and manage conversation sessions.
##
## Sessions are stored as .jsonl + .meta.json files in the project's .redclaw/ directory.
extends Node

signal session_loaded(session_id: String)
signal session_list_updated(sessions: Array)

var _current_session_id: String = ""
var _working_dir: String = ""


func _ready() -> void:
	pass


func set_working_dir(dir: String) -> void:
	_working_dir = dir


## List all sessions for the current working directory.
## Runs `python -m redclaw` to query sessions (or reads .redclaw/ directly).
func list_sessions() -> Array:
	var sessions: Array = []
	var redclaw_dir: String = _working_dir + "/.redclaw"
	var dir: DirAccess = DirAccess.open(redclaw_dir)
	if dir == null:
		return sessions

	dir.list_dir_begin()
	var file_name: String = dir.get_next()
	while file_name != "":
		if file_name.ends_with(".meta.json"):
			var full_path: String = redclaw_dir + "/" + file_name
			var f: FileAccess = FileAccess.open(full_path, FileAccess.READ)
			if f != null:
				var json_str: String = f.get_as_text()
				f.close()
				var parsed: Variant = JSON.parse_string(json_str)
				if parsed != null:
					sessions.append(parsed)
		file_name = dir.get_next()
	dir.list_dir_end()

	# Sort by updated_at (newest first)
	sessions.sort_custom(func(a, b): return a.get("updated_at", 0) > b.get("updated_at", 0))
	return sessions


## Create a new session.
func new_session() -> String:
	import_time
	var session_id: String = str(import_time.get_ticks_msec())
	_current_session_id = session_id
	session_loaded.emit(session_id)
	return session_id


## Get the current session ID.
func get_current_session_id() -> String:
	return _current_session_id


## Switch to a different session.
func switch_session(session_id: String) -> void:
	_current_session_id = session_id
	session_loaded.emit(session_id)


## Delete a session.
func delete_session(session_id: String) -> bool:
	var redclaw_dir: String = _working_dir + "/.redclaw"
	var deleted: bool = false
	for ext in [".jsonl", ".meta.json"]:
		var path: String = redclaw_dir + "/" + session_id + ext
		if FileAccess.file_exists(path):
			DirAccess.remove_absolute(path)
			deleted = true
	if deleted:
		session_list_updated.emit(list_sessions())
	return deleted
