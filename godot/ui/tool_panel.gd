## Tool panel — displays tool call results with collapsible sections.
extends VBoxContainer

const MAX_ENTRIES: int = 20

@onready var tool_content: VBoxContainer = $ToolScroll/ToolContent


func _ready() -> void:
	pass


func add_tool_result(tool_name: String, result: String, is_error: bool) -> void:
	var panel: PanelContainer = PanelContainer.new()
	var style: StyleBoxFlat = StyleBoxFlat.new()
	style.bg_color = Color(0.1, 0.1, 0.14, 1.0) if not is_error else Color(0.2, 0.1, 0.1, 1.0)
	style.set_corner_radius_all(4)
	style.content_margin_top = 6
	style.content_margin_bottom = 6
	style.content_margin_left = 8
	style.content_margin_right = 8
	panel.add_theme_stylebox_override("panel", style)

	var vbox: VBoxContainer = VBoxContainer.new()

	var header: Label = Label.new()
	header.text = tool_name
	header.add_theme_color_override("font_color", Color(0.9, 0.22, 0.27) if not is_error else Color.RED)
	vbox.add_child(header)

	var result_label: RichTextLabel = RichTextLabel.new()
	result_label.bbcode_enabled = true
	result_label.fit_content = true
	result_label.custom_minimum_size.y = 20
	result_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL

	# Truncate very long results
	var display_text: String = result
	if display_text.length() > 3000:
		display_text = display_text.substr(0, 3000) + "\n... [truncated]"

	result_label.text = _escape_bbcode(display_text)
	vbox.add_child(result_label)

	panel.add_child(vbox)
	tool_content.add_child(panel)

	# Remove old entries if too many
	while tool_content.get_child_count() > MAX_ENTRIES:
		var oldest: Node = tool_content.get_child(0)
		tool_content.remove_child(oldest)
		oldest.queue_free()


func clear() -> void:
	for child in tool_content.get_children():
		tool_content.remove_child(child)
		child.queue_free()


func _escape_bbcode(text: String) -> String:
	return text.replace("[", "[lb]").replace("]", "[rb]")
