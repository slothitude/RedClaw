## SimPanel — simulation viewport with overlay controls.
##
## Contains a SubViewport rendering SimController entities,
## plus play/pause, reset, speed controls, and metrics display.
extends VBoxContainer

var _sim_controller: Node2D
var _bridge: Node
var _play_btn: Button
var _reset_btn: Button
var _speed_slider: HSlider
var _entity_label: Label
var _stability_label: Label
var _tick_label: Label
var _viewport: SubViewport


func setup(bridge: Node) -> void:
	_bridge = bridge

	# Build layout
	var container: SubViewportContainer = SubViewportContainer.new()
	container.size_flags_vertical = Control.SIZE_EXPAND_FILL
	container.stretch = true
	add_child(container)

	_viewport = SubViewport.new()
	_viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	container.add_child(_viewport)

	_sim_controller = Node2D.new()
	_sim_controller.set_script(load("res://scripts/sim_controller.gd"))
	_sim_controller.name = "SimController"
	_viewport.add_child(_sim_controller)

	# Overlay controls
	var controls: HBoxContainer = HBoxContainer.new()
	controls.add_theme_constant_override("separation", 8)
	add_child(controls)

	_play_btn = Button.new()
	_play_btn.text = "Pause"
	_play_btn.pressed.connect(_on_play_pause)
	controls.add_child(_play_btn)

	_reset_btn = Button.new()
	_reset_btn.text = "Reset"
	_reset_btn.pressed.connect(_on_reset)
	controls.add_child(_reset_btn)

	controls.add_child(Label.new())
	var speed_lbl: Label = controls.get_child(controls.get_child_count() - 1)
	speed_lbl.text = "Speed:"

	_speed_slider = HSlider.new()
	_speed_slider.min_value = 0.1
	_speed_slider.max_value = 5.0
	_speed_slider.step = 0.1
	_speed_slider.value = 1.0
	_speed_slider.custom_minimum_size = Vector2(120, 0)
	controls.add_child(_speed_slider)

	_entity_label = Label.new()
	_entity_label.text = "Entities: 0"
	_entity_label.add_theme_color_override("font_color", Color(0.6, 0.6, 0.7))
	controls.add_child(_entity_label)

	_stability_label = Label.new()
	_stability_label.text = "Stability: -"
	_stability_label.add_theme_color_override("font_color", Color(0.6, 0.6, 0.7))
	controls.add_child(_stability_label)

	_tick_label = Label.new()
	_tick_label.text = "Tick: 0"
	_tick_label.add_theme_color_override("font_color", Color(0.6, 0.6, 0.7))
	controls.add_child(_tick_label)

	# Connect bridge signals
	if _bridge:
		_bridge.sim_entity_spawned.connect(_on_entity_spawned)
		_bridge.sim_entity_removed.connect(_on_entity_removed)
		_bridge.sim_tick_received.connect(_on_sim_tick)
		_bridge.sim_reset.connect(_on_sim_reset)


func _on_play_pause() -> void:
	if _play_btn.text == "Pause":
		_play_btn.text = "Play"
		_bridge.sim_command("stop")
	else:
		_play_btn.text = "Pause"
		_bridge.sim_command("start")


func _on_reset() -> void:
	_sim_controller.clear_all()
	_bridge.sim_command("reset")


func _on_entity_spawned(entity_id: String, entity_type: String, x: float, y: float) -> void:
	_sim_controller.spawn_entity(entity_id, entity_type, x, y)
	_entity_label.text = "Entities: %d" % _sim_controller.get_entity_count()


func _on_entity_removed(entity_id: String) -> void:
	_sim_controller.remove_entity(entity_id)
	_entity_label.text = "Entities: %d" % _sim_controller.get_entity_count()


func _on_sim_tick(tick: int, positions: Dictionary, metrics: Dictionary) -> void:
	_sim_controller.update_positions(positions)
	_tick_label.text = "Tick: %d" % tick
	var stability: float = metrics.get("stability", 0.0)
	_stability_label.text = "Stability: %.0f%%" % (stability * 100.0)
	_entity_label.text = "Entities: %d" % metrics.get("total_entities", 0)


func _on_sim_reset() -> void:
	_sim_controller.clear_all()
	_tick_label.text = "Tick: 0"
	_entity_label.text = "Entities: 0"
	_stability_label.text = "Stability: -"
