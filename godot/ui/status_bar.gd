## Status bar — shows model, token counts, permission mode, connection status.
extends HBoxContainer

@onready var status_label: Label = $StatusLabel
@onready var token_label: Label = $TokenLabel
@onready var perm_label: Label = $PermLabel

var _input_tokens: int = 0
var _output_tokens: int = 0
var _model: String = ""
var _connected: bool = false
var _perm_mode: String = "ask"


func _ready() -> void:
	_update_display()


func set_connection(connected: bool) -> void:
	_connected = connected
	_update_display()


func set_model(model: String) -> void:
	_model = model
	_update_display()


func set_tokens(input_t: int, output_t: int) -> void:
	_input_tokens = input_t
	_output_tokens = output_t
	_update_display()


func set_perm_mode(mode: String) -> void:
	_perm_mode = mode
	_update_display()


func _update_display() -> void:
	var status_icon: String = "[color=green]●[/color]" if _connected else "[color=red]●[/color]"
	var model_text: String = _model if _model != "" else "no model"
	status_label.text = status_icon + " " + model_text
	status_label.add_theme_color_override("font_color", Color(0.5, 0.5, 0.58))

	var in_str: String = _format_tokens(_input_tokens)
	var out_str: String = _format_tokens(_output_tokens)
	token_label.text = "Tokens: %s in / %s out" % [in_str, out_str]
	token_label.add_theme_color_override("font_color", Color(0.5, 0.5, 0.58))

	perm_label.text = "Mode: " + _perm_mode
	perm_label.add_theme_color_override("font_color", Color(0.5, 0.5, 0.58))


func _format_tokens(n: int) -> String:
	if n >= 1000:
		return "%.1fk" % (float(n) / 1000.0)
	return str(n)
