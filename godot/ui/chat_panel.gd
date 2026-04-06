## Chat panel — handles message display, streaming text, and input.
extends VBoxContainer

@export var agent_bridge_path: NodePath

var _bridge: Node = null
var _current_assistant_label: RichTextLabel = null
var _current_assistant_text: String = ""
var _current_tool_panel: PanelContainer = null
var _is_streaming: bool = false
var _plan_mode_active: bool = false

@onready var chat_scroll: ScrollContainer = $ChatScroll
@onready var chat_messages: VBoxContainer = $ChatScroll/ChatMessages
@onready var input_field: TextEdit = $InputBar/InputField
@onready var send_button: Button = $InputBar/SendButton
@onready var plan_btn: Button = $ActionButtons/PlanBtn
@onready var go_btn: Button = $ActionButtons/GoBtn
@onready var compact_btn: Button = $ActionButtons/CompactBtn


func _ready() -> void:
	send_button.pressed.connect(_on_send)
	input_field.gui_input.connect(_on_input_gui_input)
	plan_btn.pressed.connect(_on_plan_btn)
	go_btn.pressed.connect(_on_go_btn)
	compact_btn.pressed.connect(_on_compact_btn)


func setup(bridge: Node) -> void:
	_bridge = bridge
	_bridge.text_delta_received.connect(_on_text_delta)
	_bridge.tool_call_received.connect(_on_tool_call)
	_bridge.tool_result_received.connect(_on_tool_result)
	_bridge.turn_finished.connect(_on_turn_finished)
	_bridge.error_occurred.connect(_on_error)


func set_plan_mode(active: bool) -> void:
	_plan_mode_active = active
	_update_input_style()


func _update_input_style() -> void:
	var style: StyleBoxFlat = StyleBoxFlat.new()
	if _plan_mode_active:
		style.border_color = Color(0.9, 0.7, 0.1)
		style.set_border_width_all(2)
		style.bg_color = Color(0.15, 0.13, 0.08)
	else:
		style.bg_color = Color(0.12, 0.12, 0.16)
	style.set_corner_radius_all(4)
	input_field.add_theme_stylebox_override("normal", style)
	input_field.add_theme_stylebox_override("focus", style)


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


func _on_plan_btn() -> void:
	if _bridge and _bridge.is_connected:
		_bridge.send_prompt("/plan")


func _on_go_btn() -> void:
	if _bridge and _bridge.is_connected:
		_bridge.send_prompt("/go")


func _on_compact_btn() -> void:
	if _bridge and _bridge.is_connected:
		_bridge.compact()


func _add_user_message(text: String) -> void:
	var label: RichTextLabel = RichTextLabel.new()
	label.bbcode_enabled = true
	label.fit_content = true
	label.scroll_following = true
	label.text = "[color=#88aaff][b]You:[/b] [/color] " + _escape_bbcode(text)
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
	var result: String = text

	# Escape bbcode brackets FIRST (before inserting any bbcode tags)
	result = result.replace("[", "[lb]").replace("]", "[rb]")

	# Fenced code blocks: ```lang\n...\n``` → [bgcolor][code]...[/code][/bgcolor]
	var code_block_re: RegEx = RegEx.create_from_string("```[a-zA-Z]*\\n([\\s\\S]*?)```")
	result = code_block_re.sub(result, "[bgcolor=#1a1a2e][code]$1[/code][/bgcolor]", true)

	# Inline code: `text` → [code]text[/code]
	var code_re: RegEx = RegEx.create_from_string("`([^`\\n]+?)`")
	result = code_re.sub(result, "[code]$1[/code]", true)

	# Headers: ## text → [font_size=18][b]text[/b][/font_size]
	var h2_re: RegEx = RegEx.create_from_string("^## (.+)$", true)
	result = h2_re.sub(result, "[font_size=18][b]$1[/b][/font_size]", true)

	# H3: ### text → [font_size=15][b]text[/b][/font_size]
	var h3_re: RegEx = RegEx.create_from_string("^### (.+)$", true)
	result = h3_re.sub(result, "[font_size=15][b]$1[/b][/font_size]", true)

	# Bold: **text** → [b]text[/b]
	var bold_re: RegEx = RegEx.create_from_string("\\*\\*(.+?)\\*\\*")
	result = bold_re.sub(result, "[b]$1[/b]", true)

	# Italic: *text* → [i]text[/i]
	var italic_re: RegEx = RegEx.create_from_string("\\*(.+?)\\*")
	result = italic_re.sub(result, "[i]$1[/i]", true)

	# Bullet lists: - text or * text → bullet
	var bullet_re: RegEx = RegEx.create_from_string("^[\\-\\*] (.+)$", true)
	result = bullet_re.sub(result, "[color=#e63847]•[/color] $1", true)

	# Numbered lists: 1. text → numbered
	var num_re: RegEx = RegEx.create_from_string("^(\\d+)\\. (.+)$", true)
	result = num_re.sub(result, "[color=#e63847]$1.[/color] $2", true)

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
