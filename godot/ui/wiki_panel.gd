## Wiki panel — query and display wiki knowledge base results.
extends VBoxContainer

var _bridge: Node = null

@onready var query_input: LineEdit = $QueryBar/QueryInput
@onready var query_btn: Button = $QueryBar/QueryBtn
@onready var stats_btn: Button = $QueryBar/StatsBtn
@onready var result_content: RichTextLabel = $ResultScroll/ResultContent
@onready var stats_label: Label = $StatsLabel


func _ready() -> void:
	query_btn.pressed.connect(_on_query)
	stats_btn.pressed.connect(_on_stats)
	query_input.text_submitted.connect(func(_t): _on_query())


func setup(bridge: Node) -> void:
	_bridge = bridge
	_bridge.wiki_result.connect(_on_wiki_result)


func _on_query() -> void:
	var question: String = query_input.text.strip_edges()
	if question == "" or _bridge == null:
		return
	result_content.text = "[i]Querying wiki...[/i]"
	_bridge.wiki_query(question)


func _on_stats() -> void:
	if _bridge == null:
		return
	result_content.text = "[i]Loading stats...[/i]"
	# wiki_stats returns the result directly, not via the wiki_result signal
	# We'll use the result signal which fires on JSON-RPC response
	_bridge.wiki_stats()


func _on_wiki_result(answer: String) -> void:
	result_content.text = _escape_bbcode(answer)


func _escape_bbcode(text: String) -> String:
	return text.replace("[", "[lb]").replace("]", "[rb]")
