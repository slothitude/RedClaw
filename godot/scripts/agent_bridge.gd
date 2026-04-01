## Manages the Python subprocess and JSON-RPC communication.
##
## Spawns `python -m redclaw --mode rpc` and communicates via
## JSON-RPC requests on stdin, JSONL events on stdout.
extends Node

signal text_delta_received(text: String)
signal tool_call_received(tool_id: String, tool_name: String, tool_input: String)
signal tool_result_received(tool_id: String, result: String, is_error: bool)
signal usage_received(input_tokens: int, output_tokens: int)
signal turn_finished(error: String)
signal error_occurred(message: String)
signal connection_status_changed(connected: bool)
signal ready_received(session_id: String, model: String, provider: String)

var _process_id: int = -1
var _pipe_fd: int = -1
var _thread: Thread = null
var _running: bool = false
var _request_id: int = 0
var _connected: bool = false

# Buffer for incomplete lines from stdout
var _line_buffer: String = ""


func _ready() -> void:
	pass


func _exit_tree() -> void:
	stop()


## Start the Python agent subprocess.
func start(provider: String, model: String, base_url: String = "", perm_mode: String = "ask", session_id: String = "", working_dir: String = "") -> bool:
	if _running:
		push_warning("Agent bridge already running")
		return false

	var args: PackedStringArray = ["-m", "redclaw", "--mode", "rpc"]
	args.append_array(["--provider", provider])
	args.append_array(["--model", model])
	if base_url != "":
		args.append_array(["--base-url", base_url])
	args.append_array(["--permission-mode", perm_mode])
	if session_id != "":
		args.append_array(["--session", session_id])
	if working_dir != "":
		args.append_array(["--working-dir", working_dir])

	_process_id = OS.create_process("python", args, true)
	if _process_id == -1:
		error_occurred.emit("Failed to start Python process")
		return false

	_running = true

	# Start reader thread
	_thread = Thread.new()
	_thread.start(_read_stdout)

	return true


## Stop the Python subprocess.
func stop() -> void:
	_running = false
	if _process_id != -1:
		OS.kill(_process_id)
		_process_id = -1
	if _thread and _thread.is_started():
		_thread.wait_to_finish()
		_thread = null
	_connected = false
	connection_status_changed.emit(false)


## Send a prompt to the agent.
func send_prompt(text: String) -> void:
	_send_request("prompt", {"text": text})


## Abort the current turn.
func abort() -> void:
	_send_request("abort", {})


## Create a new session.
func new_session() -> void:
	_send_request("new_session", {})


## Compact the conversation history.
func compact() -> void:
	_send_request("compact", {})


## Get current state.
func get_state() -> void:
	_send_request("get_state", {})


## Set the model.
func set_model(model: String) -> void:
	_send_request("set_model", {"model": model})


## Set the provider.
func set_provider(provider: String, base_url: String = "") -> void:
	var params: Dictionary = {"provider": provider}
	if base_url != "":
		params["base_url"] = base_url
	_send_request("set_provider", params)


func _send_request(method: String, params: Dictionary = {}) -> void:
	_request_id += 1
	var request: Dictionary = {
		"jsonrpc": "2.0",
		"id": _request_id,
		"method": method,
		"params": params
	}
	var json_str: String = JSON.stringify(request)
	OS.write_stdout(_process_id, (json_str + "\n").to_utf8_buffer())


## Read stdout in a background thread.
func _read_stdout() -> void:
	while _running:
		# Small sleep to avoid busy-waiting
		OS.delay_msec(50)

		var output: PackedByteArray = OS.read_stdout(_process_id, 4096)
		if output.size() == 0:
			continue

		var chunk: String = output.get_string_from_utf8()
		_line_buffer += chunk

		# Process complete lines
		while _line_buffer.find("\n") != -1:
			var idx: int = _line_buffer.find("\n")
			var line: String = _line_buffer.substr(0, idx).strip_edges()
			_line_buffer = _line_buffer.substr(idx + 1)
			if line != "":
				_handle_line.call_deferred(line)


## Handle a single JSONL line from the agent.
func _handle_line(line: String) -> void:
	var parsed: Variant = JSON.parse_string(line)
	if parsed == null:
		return

	var obj: Dictionary = parsed as Dictionary
	if obj == null:
		return

	# Handle JSON-RPC responses (have "result" or "error")
	if obj.has("id") and (obj.has("result") or obj.has("error")):
		# JSON-RPC response — we don't need to do much with these for now
		return

	# Handle streaming events
	var event_type: String = obj.get("type", "")

	match event_type:
		"ready":
			_connected = true
			connection_status_changed.emit(true)
			ready_received.emit(
				obj.get("session_id", ""),
				obj.get("model", ""),
				obj.get("provider", "")
			)
		"text_delta":
			text_delta_received.emit(obj.get("text", ""))
		"tool_call":
			tool_call_received.emit(
				obj.get("id", ""),
				obj.get("name", ""),
				obj.get("input", "")
			)
		"tool_result":
			tool_result_received.emit(
				obj.get("id", ""),
				obj.get("result", ""),
				obj.get("is_error", false)
			)
		"usage":
			usage_received.emit(
				int(obj.get("input_tokens", 0)),
				int(obj.get("output_tokens", 0))
			)
		"done":
			turn_finished.emit(obj.get("error", ""))
		"error":
			error_occurred.emit(obj.get("message", "Unknown error"))


var is_connected: bool:
	get:
		return _connected
