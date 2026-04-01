## Chat panel — handles message display, streaming text, and input.
extends VBoxContainer

@export var agent_bridge_path: NodePath

var _bridge: Node = null
var _current_assistant_label: RichTextLabel = null
var _current_assistant_text: String = ""
var _current_tool_panel: PanelContainer = null
var _is_streaming: bool = false

@onready var chat_scroll: ScrollContainer = $ChatScroll
@onready var chat_messages: VBoxContainer = $ChatScroll/ChatMessages
@onready var input_field: TextEdit = $InputBar/InputField
@onready var send_button: Button = $InputBar/SendButton


func _ready() -> void:
	send_button.pressed.connect(_on_send)
	input_field.gui_input.connect(_on_input_gui_input)


func setup(bridge: Node) -> void:
	_bridge = bridge
	_bridge.text_delta_received.connect(_on_text_delta)
	_bridge.tool_call_received.connect(_on_tool_call)
	_bridge.tool_result_received.connect(_on_tool_result)
	_bridge.turn_finished.connect(_on_turn_finished)
	_bridge.error_occurred.connect(_on_error)


func _on_input_gui_input(event: InputEvent) -> void:
	# Send on Enter (without Shift)
	if event is InputEventKey and event.pressed:
		var key_event: InputEventKey = event as InputEventKey
		if key_event.keycode == KEY_ENTER and not key_event.shift_pressed:
			_on_send()
			input_field.accept_event()


func _on_send() -> void:
	var text: String = input_field.text.strip_edges()
	if text == "" or _bridge == null:
		return

	# Add user bubble
	_add_user_message(text)

	# Clear input
	input_field.text = ""

	# Start streaming
	_is_streaming = true
	_current_assistant_text = ""
	_current_assistant_label = _add_assistant_bubble()

	# Send to agent
	_bridge.send_prompt(text)


func _add_user_message(text: String) -> void:
	var label: RichTextLabel = RichTextLabel.new()
	label.bbcode_enabled = true
	label.fit_content = true
	label.scroll_following = true
	label.text = "[color=#88aaff][b>You:[/b> [/color> " + _escape_bbcode(text)
	label.custom_minimum_size.y = 40
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL

	var panel: PanelContainer = PanelContainer.new()
	panel.add_theme_stylebox_override("panel", _make_style(Color(0.2, 0.22, 0.3, 1.0)))
	panel.add_child(label)

	chat_messages.add_child(panel)
	_scroll_to_bottom()


func _add_assistant_bubble() -> RichTextLabel:
	var label: RichTextLabel = RichTextLabel.new()
	label.bbcode_enabled = true
	label.fit_content = true
	label.scroll_following = true
	label.custom_minimum_size.y = 40
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL

	var panel: PanelContainer = PanelContainer.new()
	panel.add_theme_stylebox_override("panel", _make_style(Color(0.15, 0.15, 0.2, 1.0)))
	panel.add_child(label)

	chat_messages.add_child(panel)
	_scroll_to_bottom()
	return label


func _on_text_delta(text: String) -> void:
	if _current_assistant_label == null:
		_current_assistant_label = _add_assistant_bubble()
	_current_assistant_text += text
	# Simple markdown → bbcode conversion
	_current_assistant_label.text = _markdown_to_bbcode(_current_assistant_text)
	_scroll_to_bottom()


func _on_tool_call(tool_id: String, tool_name: String, tool_input: String) -> void:
	# Add a collapsible tool call section
	_current_tool_panel = PanelContainer.new()
	_current_tool_panel.add_theme_stylebox_override("panel", _make_style(Color(0.18, 0.18, 0.24, 1.0)))

	var vbox: VBoxContainer = VBoxContainer.new()

	var header: Label = Label.new()
	header.text = "▶ " + tool_name
	header.add_theme_color_override("font_color", Color(0.9, 0.22, 0.27))

	var input_label: RichTextLabel = RichTextLabel.new()
	input_label.bbcode_enabled = true
	input_label.fit_content = true
	input_label.custom_minimum_size.y = 20
	input_label.text = _escape_bbcode(tool_input.left(500))

	vbox.add_child(header)
	vbox.add_child(input_label)
	_current_tool_panel.add_child(vbox)
	chat_messages.add_child(_current_tool_panel)
	_scroll_to_bottom()


func _on_tool_result(tool_id: String, result: String, is_error: bool) -> void:
	if _current_tool_panel == null:
		return

	var vbox: VBoxContainer = _current_tool_panel.get_child(0) as VBoxContainer
	if vbox == null:
		return

	var result_label: RichTextLabel = RichTextLabel.new()
	result_label.bbcode_enabled = true
	result_label.fit_content = true
	result_label.custom_minimum_size.y = 20
	var color: String = "#ff6666" if is_error else "#aaaaaa"
	result_label.text = "[color=" + color + "]" + _escape_bbcode(result.left(2000)) + "[/color]"
	vbox.add_child(result_label)
	_scroll_to_bottom()


func _on_turn_finished(error: String) -> void:
	_is_streaming = false
	_current_assistant_label = null
	_current_assistant_text = ""
	_current_tool_panel = null


func _on_error(message: String) -> void:
	var label: Label = Label.new()
	label.text = "Error: " + message
	label.add_theme_color_override("font_color", Color.RED)
	chat_messages.add_child(label)
	_scroll_to_bottom()
	_is_streaming = false


func _scroll_to_bottom() -> void:
	await get_tree().process_frame
	chat_scroll.ensure_control_visible(chat_messages.get_child(chat_messages.get_child_count() - 1) as Control)


func _escape_bbcode(text: String) -> String:
	return text.replace("[", "[lb]").replace("]", "[rb]")


func _markdown_to_bbcode(text: String) -> String:
	# Basic markdown → bbcode conversion
	var result: String = text
	result = result.replace("[", "[lb=").replace("]", "[rb=")
	# Bold
	var bold_regex: RegEx = RegEx.create_from_string("\\*\\*(.+?)\\*\\*")
	result = bold_regex.sub(result, "[b]$1[/b]", true)
	# Italic
	var italic_regex: RegEx = RegEx.create_from_string("\\*(.+?)\\*")
	result = italic_regex.sub(result, "[i]$1[/i]", true)
	# Code
	var code_regex: RegEx = RegEx.create_from_string("`(.+?)`")
	result = code_regex.sub(result, "[code]$1[/code]", true)
	return result


func _make_style(color: Color) -> StyleBoxFlat:
	var style: StyleBoxFlat = StyleBoxFlat.new()
	style.bg_color = color
	style.set_corner_radius_all(6)
	style.content_margin_top = 8
	style.content_margin_bottom = 8
	style.content_margin_left = 12
	style.content_margin_right = 12
	return style
