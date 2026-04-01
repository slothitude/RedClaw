## Settings dialog — provider/API key/model configuration.
extends AcceptDialog

signal settings_applied(settings: Dictionary)

@onready var provider_opt: OptionButton = $VBox/ProviderOpt
@onready var base_url_input: LineEdit = $VBox/BaseUrlInput
@onready var model_input: LineEdit = $VBox/ModelInput
@onready var api_key_input: LineEdit = $VBox/ApiKeyInput
@onready var perm_opt: OptionButton = $VBox/PermOpt
@onready var work_dir_input: LineEdit = $VBox/WorkDirInput


func _ready() -> void:
	# Populate options
	var providers: Array = ["openai", "anthropic", "ollama", "groq", "deepseek", "openrouter"]
	for p in providers:
		provider_opt.add_item(p)

	var modes: Array = ["ask", "read_only", "workspace_write", "danger_full_access"]
	for m in modes:
		perm_opt.add_item(m)

	confirmed.connect(_on_confirmed)


func get_settings() -> Dictionary:
	return {
		"provider": provider_opt.get_item_text(provider_opt.selected),
		"base_url": base_url_input.text,
		"model": model_input.text,
		"api_key": api_key_input.text,
		"perm_mode": perm_opt.get_item_text(perm_opt.selected),
		"working_dir": work_dir_input.text,
	}


func set_settings(settings: Dictionary) -> void:
	if settings.has("provider"):
		for i in range(provider_opt.item_count):
			if provider_opt.get_item_text(i) == settings["provider"]:
				provider_opt.select(i)
				break
	if settings.has("base_url"):
		base_url_input.text = settings["base_url"]
	if settings.has("model"):
		model_input.text = settings["model"]
	if settings.has("api_key"):
		api_key_input.text = settings["api_key"]
	if settings.has("perm_mode"):
		for i in range(perm_opt.item_count):
			if perm_opt.get_item_text(i) == settings["perm_mode"]:
				perm_opt.select(i)
				break
	if settings.has("working_dir"):
		work_dir_input.text = settings["working_dir"]


func _on_confirmed() -> void:
	settings_applied.emit(get_settings())
