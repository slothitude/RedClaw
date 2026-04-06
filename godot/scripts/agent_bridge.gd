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
signal plan_mode_changed(enabled: bool)
signal wiki_result(answer: String)

var _pid: int = -1
var _stdin_pipe: FileAccess = null
var _stdout_pipe: FileAccess = null
var _thread: Thread = null
var _running: bool = false
var _request_id: int = 0
var _connected: bool = false

# Buffer for incomplete lines from stdout
var _line_buffer: String = ""

# Pending JSON-RPC response callbacks keyed by request id
var _pending_callbacks: Dictionary = {}


var is_connected: bool:
	get:
		return _connected


func _exit_tree() -> void:
	stop()


## Start the Python agent subprocess.
func start(provider: String, model: String, base_url: String = "", perm_mode: String = "ask", session_id: String = "", working_dir: String = "", assistant_mode: bool = false) -> bool:
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
	if assistant_mode:
		args.append_array(["--assistant"])

	var result: Dictionary = OS.execute_with_pipe("python", args, false)
	if result.is_empty() or not result.has("pid"):
		error_occurred.emit("Failed to start Python process")
		return false

	_pid = result["pid"]
	_stdin_pipe = result["stdin"]
	_stdout_pipe = result["stdout"]

	_running = true
	_connected = true
	connection_status_changed.emit(true)

	# Start reader thread
	_thread = Thread.new()
	_thread.start(_read_stdout)

	return true


## Stop the Python subprocess.
func stop() -> void:
	_running = false
	if _pid != -1:
		OS.kill(_pid)
		_pid = -1
	if _stdin_pipe:
		_stdin_pipe.close()
		_stdin_pipe = null
	if _stdout_pipe:
		_stdout_pipe.close()
		_stdout_pipe = null
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


## Toggle plan mode.
func plan_mode(enabled: bool) -> void:
	_send_request("plan_mode", {"enabled": enabled})


## Query the wiki.
func wiki_query(question: String) -> void:
	_send_request("wiki_query", {"question": question})


## Get wiki stats.
func wiki_stats() -> void:
	_send_request("wiki_stats", {})


## Ingest into the wiki.
func wiki_ingest(source: String, topic: String = "general") -> void:
	_send_request("wiki_ingest", {"source": source, "topic": topic})


## Send a JSON-RPC request.
func _send_request(method: String, params: Dictionary = {}) -> void:
	_request_id += 1
	var request: Dictionary = {
		"jsonrpc": "2.0",
		"id": _request_id,
		"method": method,
		"params": params
	}
	var json_str: String = JSON.stringify(request)
	if _stdin_pipe:
		_stdin_pipe.store_string(json_str + "\n")
		_stdin_pipe.flush()


	else:
		push_warning("Cannot send request: no stdin pipe")


## Read stdout in a background thread.
func _read_stdout() -> void:
	while _running:
		# Small sleep to avoid busy-waiting
		OS.delay_msec(50)

		if _stdout_pipe == null:
			continue

		# Read available data
		var available: int = _stdout_pipe.get_length() if _stdout_pipe.get_buffer(1).size() > 0 else 0
		if available <= 0:
			continue

		var data: PackedByteArray = _stdout_pipe.get_buffer(min(available, 65536))
		if data.size() == 0:
			continue

		var chunk: String = data.get_string_from_utf8()
		_line_buffer += chunk

		# Process complete lines
		while _line_buffer.find("\n") != -1:
			var idx: int = _line_buffer.find("\n")
			var line: String = _line_buffer.substr(0, idx).strip_edges()
			_line_buffer = _line_buffer.substr(idx + 1)
			if line != "":
				_handle_line(line)

	# Process remaining buffer after exit
	if _line_buffer.length() > 0 and not _running:
		for remaining_line in _line_buffer.split("\n"):
			if remaining_line.strip_edges() != "":
				_handle_line(remaining_line)
		_line_buffer = ""


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
		# Check for wiki_query response
		if obj.has("result") and obj["result"] is Dictionary:
			var res: Dictionary = obj["result"]
			if res.has("answer"):
				wiki_result.emit(res["answer"])
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
				JSON.stringify(obj.get("input", {}))
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
			_running = false
		"error":
			error_occurred.emit(obj.get("message", "Unknown error"))
		"plan_mode_changed":
			plan_mode_changed.emit(obj.get("enabled", false))
