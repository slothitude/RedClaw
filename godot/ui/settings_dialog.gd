## Settings dialog — provider/API key/model/assistant configuration.
extends AcceptDialog

signal settings_applied(settings: Dictionary)

@onready var provider_opt: OptionButton = $VBox/ProviderOpt
@onready var base_url_input: LineEdit = $VBox/BaseUrlInput
@onready var model_input: LineEdit = $VBox/ModelInput
@onready var api_key_input: LineEdit = $VBox/ApiKeyInput
@onready var perm_opt: OptionButton = $VBox/PermOpt
@onready var work_dir_input: LineEdit = $VBox/WorkDirInput

# Assistant fields
@onready var assistant_check: CheckBox = $VBox/AssistantCheck
@onready var persona_name_input: LineEdit = $VBox/PersonaNameInput
@onready var timezone_input: LineEdit = $VBox/TimezoneInput
@onready var briefing_time_input: LineEdit = $VBox/BriefingTimeInput
@onready var briefing_check: CheckBox = $VBox/BriefingCheck
@onready var weather_input: LineEdit = $VBox/WeatherInput
@onready var briefing_weather_check: CheckBox = $VBox/BriefingWeatherCheck
@onready var briefing_news_check: CheckBox = $VBox/BriefingNewsCheck
@onready var briefing_tasks_check: CheckBox = $VBox/BriefingTasksCheck
@onready var news_topics_input: LineEdit = $VBox/NewsTopicsInput


func _ready() -> void:
	# Populate options
	var providers: Array = ["openai", "anthropic", "ollama", "groq", "deepseek", "openrouter", "zai"]
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
		# Assistant / persona
		"assistant_mode": assistant_check.button_pressed,
		"persona_name": persona_name_input.text,
		"timezone": timezone_input.text,
		"briefing_time": briefing_time_input.text,
		"briefing_enabled": briefing_check.button_pressed,
		"weather_location": weather_input.text,
		"briefing_weather": briefing_weather_check.button_pressed,
		"briefing_news": briefing_news_check.button_pressed,
		"briefing_tasks": briefing_tasks_check.button_pressed,
		"news_topics": news_topics_input.text,
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
	# Assistant / persona
	if settings.has("assistant_mode"):
		assistant_check.button_pressed = settings["assistant_mode"]
	if settings.has("persona_name"):
		persona_name_input.text = settings["persona_name"]
	if settings.has("timezone"):
		timezone_input.text = settings["timezone"]
	if settings.has("briefing_time"):
		briefing_time_input.text = settings["briefing_time"]
	if settings.has("briefing_enabled"):
		briefing_check.button_pressed = settings["briefing_enabled"]
	if settings.has("weather_location"):
		weather_input.text = settings["weather_location"]
	if settings.has("briefing_weather"):
		briefing_weather_check.button_pressed = settings["briefing_weather"]
	if settings.has("briefing_news"):
		briefing_news_check.button_pressed = settings["briefing_news"]
	if settings.has("briefing_tasks"):
		briefing_tasks_check.button_pressed = settings["briefing_tasks"]
	if settings.has("news_topics"):
		news_topics_input.text = settings["news_topics"]


func _on_confirmed() -> void:
	settings_applied.emit(get_settings())
