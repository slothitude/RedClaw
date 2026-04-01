## Message item — renders a single message (user/assistant/tool) with rich text.
extends PanelContainer

enum Role { USER, ASSISTANT, TOOL_CALL, TOOL_RESULT }

var _role: Role = Role.USER
var _text: String = ""

@onready var role_label: Label = $VBox/RoleLabel
@onready var content_label: RichTextLabel = $VBox/ContentLabel


func _ready() -> void:
	pass


func setup(role: Role, text: String, tool_name: String = "") -> void:
	_role = role
	_text = text

	var style: StyleBoxFlat = StyleBoxFlat.new()
	style.set_corner_radius_all(6)
	style.content_margin_top = 8
	style.content_margin_bottom = 8
	style.content_margin_left = 12
	style.content_margin_right = 12

	match role:
		Role.USER:
			role_label.text = "You"
			role_label.add_theme_color_override("font_color", Color(0.53, 0.67, 1.0))
			style.bg_color = Color(0.2, 0.22, 0.3, 1.0)
			content_label.text = _escape_bbcode(text)
		Role.ASSISTANT:
			role_label.text = "RedClaw"
			role_label.add_theme_color_override("font_color", Color(0.9, 0.22, 0.27))
			style.bg_color = Color(0.15, 0.15, 0.2, 1.0)
			content_label.text = _escape_bbcode(text)
		Role.TOOL_CALL:
			role_label.text = "▶ " + tool_name
			role_label.add_theme_color_override("font_color", Color(0.9, 0.22, 0.27))
			style.bg_color = Color(0.18, 0.18, 0.24, 1.0)
			content_label.text = _escape_bbcode(text.left(500))
		Role.TOOL_RESULT:
			role_label.text = "Result"
			role_label.add_theme_color_override("font_color", Color(0.7, 0.7, 0.7))
			style.bg_color = Color(0.12, 0.12, 0.16, 1.0)
			content_label.text = _escape_bbcode(text.left(2000))

	add_theme_stylebox_override("panel", style)


func append_text(delta: String) -> void:
	_text += delta
	content_label.text = _escape_bbcode(_text)


func _escape_bbcode(text: String) -> String:
	return text.replace("[", "[lb]").replace("]", "[rb]")
