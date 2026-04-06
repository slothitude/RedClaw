## Main — app lifecycle, window setup, wires everything together.
extends Control

const SIDEBAR_SCENE: PackedScene = preload("res://ui/sidebar.tscn")
const CHAT_PANEL_SCENE: PackedScene = preload("res://ui/chat_panel.tscn")
const TOOL_PANEL_SCENE: PackedScene = preload("res://ui/tool_panel.tscn")
const WIKI_PANEL_SCENE: PackedScene = preload("res://ui/wiki_panel.tscn")
const SETTINGS_DIALOG_SCENE: PackedScene = preload("res://ui/settings_dialog.tscn")

var _agent_bridge: Node
var _session_manager: Node
var _settings_mgr: Node
var _sidebar: VBoxContainer
var _chat_panel: VBoxContainer
var _tool_panel: VBoxContainer
var _wiki_panel: VBoxContainer
var _sim_panel: VBoxContainer
var _sim_controller: Node2D
var _right_tabs: TabContainer
var _status_bar: HBoxContainer
var _settings_dialog: AcceptDialog
var _chat_sidebar: VBoxContainer
var _chat_toggle_btn: Button
var _chat_visible: bool = true

var _input_tokens: int = 0
var _output_tokens: int = 0


func _ready() -> void:
	# Window setup
	DisplayServer.window_set_title("RedClaw — AI Coding Agent")

	# Create child nodes
	_agent_bridge = Node.new()
	_agent_bridge.set_script(load("res://scripts/agent_bridge.gd"))
	_agent_bridge.name = "AgentBridge"
	add_child(_agent_bridge)

	_session_manager = Node.new()
	_session_manager.set_script(load("res://scripts/session_manager.gd"))
	_session_manager.name = "SessionManager"
	add_child(_session_manager)

	_settings_mgr = Node.new()
	_settings_mgr.set_script(load("res://scripts/settings.gd"))
	_settings_mgr.name = "SettingsManager"
	add_child(_settings_mgr)

	# Build the IDE layout
	_build_layout()

	# Apply loaded settings
	var settings: Dictionary = _settings_mgr.get_settings()
	_apply_settings(settings)

	# Connect bridge signals
	_agent_bridge.ready_received.connect(_on_bridge_ready)
	_agent_bridge.text_delta_received.connect(_on_text_delta)
	_agent_bridge.tool_call_received.connect(_on_tool_call)
	_agent_bridge.tool_result_received.connect(_on_tool_result)
	_agent_bridge.usage_received.connect(_on_usage)
	_agent_bridge.turn_finished.connect(_on_turn_finished)
	_agent_bridge.error_occurred.connect(_on_error)
	_agent_bridge.connection_status_changed.connect(_on_connection_changed)
	_agent_bridge.plan_mode_changed.connect(_on_plan_mode_changed)

	# Auto-start with default settings if available
	_try_auto_start()


func _build_layout() -> void:
	# Main VBox
	var main_vbox: VBoxContainer = VBoxContainer.new()
	main_vbox.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(main_vbox)

	# HSplit: [sidebar | collapsible_chat | sim_panel | right_tabs]
	var hsplit: HSplitContainer = HSplitContainer.new()
	hsplit.size_flags_vertical = Control.SIZE_EXPAND_FILL
	main_vbox.add_child(hsplit)

	# Sidebar
	_sidebar = SIDEBAR_SCENE.instantiate()
	hsplit.add_child(_sidebar)
	_sidebar.setup(_session_manager)
	_sidebar.settings_changed.connect(_on_sidebar_settings_changed)
	_sidebar.session_selected.connect(_on_session_selected)

	# Collapsible chat sidebar
	_chat_sidebar = VBoxContainer.new()
	_chat_sidebar.custom_minimum_size = Vector2(300, 0)
	hsplit.add_child(_chat_sidebar)

	_chat_toggle_btn = Button.new()
	_chat_toggle_btn.text = "v Chat"
	_chat_toggle_btn.flat = true
	_chat_toggle_btn.pressed.connect(_on_toggle_chat)
	_chat_sidebar.add_child(_chat_toggle_btn)

	_chat_panel = CHAT_PANEL_SCENE.instantiate()
	_chat_sidebar.add_child(_chat_panel)
	_chat_panel.setup(_agent_bridge)

	# Simulation panel (center)
	_sim_panel = VBoxContainer.new()
	_sim_panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_sim_panel.set_script(load("res://ui/sim_panel.gd"))
	hsplit.add_child(_sim_panel)

	# Right panel: TabContainer with Tool Output + Wiki tabs
	_right_tabs = TabContainer.new()
	_right_tabs.custom_minimum_size = Vector2(250, 0)

	_tool_panel = TOOL_PANEL_SCENE.instantiate()
	_tool_panel.name = "Tool Output"
	_right_tabs.add_child(_tool_panel)

	_wiki_panel = WIKI_PANEL_SCENE.instantiate()
	_wiki_panel.name = "Wiki"
	_right_tabs.add_child(_wiki_panel)
	_wiki_panel.setup(_agent_bridge)

	hsplit.add_child(_right_tabs)

	# Setup sim panel with bridge
	_sim_panel.setup(_agent_bridge)

	# Status bar (built inline since we add plan_label)
	_status_bar = HBoxContainer.new()
	main_vbox.add_child(_status_bar)

	var status_label: Label = Label.new()
	status_label.name = "StatusLabel"
	status_label.text = "Ready"
	status_label.add_theme_color_override("font_color", Color(0.5, 0.5, 0.58))
	status_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_status_bar.add_child(status_label)

	var token_label: Label = Label.new()
	token_label.name = "TokenLabel"
	token_label.text = "Tokens: 0 in / 0 out"
	token_label.add_theme_color_override("font_color", Color(0.5, 0.5, 0.58))
	_status_bar.add_child(token_label)

	var plan_label: Label = Label.new()
	plan_label.name = "PlanLabel"
	plan_label.text = ""
	plan_label.add_theme_color_override("font_color", Color(0.9, 0.7, 0.1))
	_status_bar.add_child(plan_label)

	# Chat toggle button in status bar
	var chat_toggle_status: Button = Button.new()
	chat_toggle_status.name = "ChatToggle"
	chat_toggle_status.text = "Chat"
	chat_toggle_status.flat = true
	chat_toggle_status.pressed.connect(_on_toggle_chat)
	_status_bar.add_child(chat_toggle_status)

	var perm_label: Label = Label.new()
	perm_label.name = "PermLabel"
	perm_label.text = "Mode: ask"
	perm_label.add_theme_color_override("font_color", Color(0.5, 0.5, 0.58))
	_status_bar.add_child(perm_label)


func _try_auto_start() -> void:
	var settings: Dictionary = _settings_mgr.get_settings()
	var provider: String = settings.get("provider", "")
	var model: String = settings.get("model", "")
	if provider != "" and model != "":
		var assistant_mode: bool = settings.get("assistant_mode", false)
		start_agent(provider, model, settings.get("base_url", ""), settings.get("perm_mode", "ask"))
		# Update title
		if assistant_mode:
			var persona: String = settings.get("persona_name", "")
			DisplayServer.window_set_title("RedClaw — " + persona if persona != "" else "RedClaw — Assistant")


func start_agent(provider: String, model: String, base_url: String = "", perm_mode: String = "ask") -> void:
	# Set API key env var if stored
	var api_key: String = _settings_mgr.get_api_key(provider)
	if api_key != "":
		var env_name: String = provider.to_upper() + "_API_KEY"
		OS.set_environment(env_name, api_key)

	var work_dir: String = _settings_mgr.get_setting("working_dir", "")
	var session_id: String = _session_manager.get_current_session_id()
	var assistant_mode: bool = _settings_mgr.get_setting("assistant_mode", false)

	_agent_bridge.start(provider, model, base_url, perm_mode, session_id, work_dir, assistant_mode)


func _on_bridge_ready(session_id: String, model: String, provider: String) -> void:
	_update_status("Connected", model)


func _on_text_delta(text: String) -> void:
	pass  # Handled by chat_panel directly


func _on_tool_call(tool_id: String, tool_name: String, tool_input: String) -> void:
	pass  # Handled by chat_panel


func _on_tool_result(tool_id: String, result: String, is_error: bool) -> void:
	# Also show in tool panel
	var tool_panel_script: Node = _tool_panel
	tool_panel_script.add_tool_result("Tool", result, is_error)


func _on_usage(input_tokens: int, output_tokens: int) -> void:
	_input_tokens = input_tokens
	_output_tokens = output_tokens
	var token_label: Label = _status_bar.get_node("TokenLabel") as Label
	if token_label:
		token_label.text = "Tokens: %s in / %s out" % [_fmt_tokens(input_tokens), _fmt_tokens(output_tokens)]


func _on_turn_finished(error: String) -> void:
	if error != "":
		_update_status("Error: " + error.left(80), "")


func _on_error(message: String) -> void:
	_update_status("Error", "")


func _on_connection_changed(connected: bool) -> void:
	var status_label: Label = _status_bar.get_node("StatusLabel") as Label
	if status_label:
		status_label.text = "Connected" if connected else "Disconnected"


func _on_plan_mode_changed(enabled: bool) -> void:
	var plan_label: Label = _status_bar.get_node("PlanLabel") as Label
	if plan_label:
		plan_label.text = "PLAN" if enabled else ""
	# Update chat panel input styling
	_chat_panel.set_plan_mode(enabled)


func _on_sidebar_settings_changed(settings: Dictionary) -> void:
	# Save all settings from sidebar (including assistant mode)
	for key in settings:
		if key != "api_key":
			_settings_mgr.set_setting(key, settings[key])
	if settings.get("api_key", "") != "":
		_settings_mgr.set_api_key(settings.get("provider", "openai"), settings["api_key"])
	_settings_mgr.save_settings()

	var perm_label: Label = _status_bar.get_node("PermLabel") as Label
	if perm_label:
		perm_label.text = "Mode: " + settings.get("perm_mode", "ask")

	# Update window title based on mode
	var assistant_mode: bool = settings.get("assistant_mode", false)
	if assistant_mode:
		var persona: String = settings.get("persona_name", "")
		if persona != "":
			DisplayServer.window_set_title("RedClaw — " + persona)
		else:
			DisplayServer.window_set_title("RedClaw — Assistant")
	else:
		DisplayServer.window_set_title("RedClaw — AI Coding Agent")


func _on_session_selected(session_id: String) -> void:
	_session_manager.switch_session(session_id)
	# Restart bridge with new session
	var settings: Dictionary = _settings_mgr.get_settings()
	_agent_bridge.stop()
	start_agent(
		settings.get("provider", "openai"),
		settings.get("model", "gpt-4o"),
		settings.get("base_url", ""),
		settings.get("perm_mode", "ask"),
	)


func _apply_settings(settings: Dictionary) -> void:
	_sidebar.set_settings(settings)
	_on_sidebar_settings_changed(settings)


func _update_status(status: String, model: String) -> void:
	var status_label: Label = _status_bar.get_node("StatusLabel") as Label
	if status_label:
		var text: String = status
		if model != "":
			text += " | " + model
		status_label.text = text


func _on_toggle_chat() -> void:
	_chat_visible = not _chat_visible
	_chat_panel.visible = _chat_visible
	_chat_toggle_btn.text = ("v Chat" if _chat_visible else "> Chat")
	if _chat_toggle_btn.text == "v Chat":
		_chat_toggle_btn.text = "▾ Chat"
	else:
		_chat_toggle_btn.text = "▸ Chat"


func _fmt_tokens(n: int) -> String:
	if n >= 1000:
		return "%.1fk" % (float(n) / 1000.0)
	return str(n)
