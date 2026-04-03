## Sidebar — session list, settings controls, and assistant toggle.
extends VBoxContainer

signal settings_changed(settings: Dictionary)
signal session_selected(session_id: String)
signal new_session_requested

var _session_manager: Node = null

@onready var session_list: ItemList = $SessionList
@onready var provider_opt: OptionButton = $ProviderOpt
@onready var model_input: LineEdit = $ModelInput
@onready var api_key_input: LineEdit = $ApiKeyInput
@onready var perm_mode_opt: OptionButton = $PermModeOpt
@onready var assistant_check: CheckBox = $AssistantCheck
@onready var persona_name_input: LineEdit = $PersonaNameInput


func _ready() -> void:
	# Populate provider options
	var providers: Array = ["openai", "anthropic", "ollama", "groq", "deepseek", "openrouter", "zai"]
	for p in providers:
		provider_opt.add_item(p)

	# Populate permission mode options
	var modes: Array = ["ask", "read_only", "workspace_write", "danger_full_access"]
	for m in modes:
		perm_mode_opt.add_item(m)
	perm_mode_opt.select(0)

	# Connect signals
	provider_opt.item_selected.connect(_on_settings_changed)
	model_input.text_changed.connect(func(_t): _on_settings_changed())
	api_key_input.text_changed.connect(func(_t): _on_settings_changed())
	perm_mode_opt.item_selected.connect(_on_settings_changed)
	assistant_check.toggled.connect(func(_t): _on_settings_changed())
	persona_name_input.text_changed.connect(func(_t): _on_settings_changed())
	session_list.item_selected.connect(_on_session_selected)


func setup(session_mgr: Node) -> void:
	_session_manager = session_mgr
	refresh_sessions()


func refresh_sessions() -> void:
	session_list.clear()
	if _session_manager == null:
		return
	var sessions: Array = _session_manager.list_sessions()
	for s in sessions:
		session_list.add_item(s.get("id", "unknown"))


func get_settings() -> Dictionary:
	return {
		"provider": provider_opt.get_item_text(provider_opt.selected),
		"model": model_input.text,
		"api_key": api_key_input.text,
		"perm_mode": perm_mode_opt.get_item_text(perm_mode_opt.selected),
		"assistant_mode": assistant_check.button_pressed,
		"persona_name": persona_name_input.text,
	}


func set_settings(settings: Dictionary) -> void:
	if settings.has("provider"):
		for i in range(provider_opt.item_count):
			if provider_opt.get_item_text(i) == settings["provider"]:
				provider_opt.select(i)
				break
	if settings.has("model"):
		model_input.text = settings["model"]
	if settings.has("api_key"):
		api_key_input.text = settings["api_key"]
	if settings.has("perm_mode"):
		for i in range(perm_mode_opt.item_count):
			if perm_mode_opt.get_item_text(i) == settings["perm_mode"]:
				perm_mode_opt.select(i)
				break
	if settings.has("assistant_mode"):
		assistant_check.button_pressed = settings["assistant_mode"]
	if settings.has("persona_name"):
		persona_name_input.text = settings["persona_name"]


func _on_settings_changed(_index: int = -1) -> void:
	settings_changed.emit(get_settings())


func _on_session_selected(index: int) -> void:
	var session_id: String = session_list.get_item_text(index)
	session_selected.emit(session_id)
