## SimController — renders simulation entities in a 2D viewport.
##
## Subscribes to sim_* signals from agent_bridge and draws entities
## with smooth position lerping each frame.
extends Node2D

var _entities: Dictionary = {}  # entity_id → {target_pos, type, properties, radius, color}

var _camera: Camera2D
var _dragging: bool = false
var _drag_start: Vector2 = Vector2.ZERO
var _camera_start: Vector2 = Vector2.ZERO

const LERP_SPEED: float = 8.0
const BOUNDS: Rect2 = Rect2(-500, -500, 1000, 1000)

# Colors by entity type
const TYPE_COLORS: Dictionary = {
	"particle": Color(0.3, 0.7, 1.0, 0.9),
	"orb": Color(0.9, 0.4, 1.0, 0.85),
	"field": Color(0.2, 0.9, 0.3, 0.3),
	"constraint": Color(1.0, 0.9, 0.2, 0.7),
}


func _ready() -> void:
	_camera = Camera2D.new()
	_camera.zoom = Vector2(1.0, 1.0)
	_camera.position = Vector2.ZERO
	add_child(_camera)
	set_process(true)


func _draw() -> void:
	# Draw bounds
	draw_rect(BOUNDS, Color(0.2, 0.2, 0.25, 0.5), false, 2.0)

	# Draw grid
	var grid_color := Color(0.15, 0.15, 0.2, 0.3)
	for x in range(-500, 501, 100):
		draw_line(Vector2(x, -500), Vector2(x, 500), grid_color, 1.0)
	for y in range(-500, 501, 100):
		draw_line(Vector2(-500, y), Vector2(500, y), grid_color, 1.0)

	# Draw entities
	for entity_id in _entities:
		var e: Dictionary = _entities[entity_id]
		var pos: Vector2 = e.get("current_pos", Vector2.ZERO)
		var radius: float = e.get("radius", 10.0)
		var color: Color = e.get("color", TYPE_COLORS.get("particle", Color.WHITE))
		var etype: String = e.get("type", "particle")

		match etype:
			"particle":
				draw_circle(pos, radius, color)
				draw_circle(pos, radius, Color(color.r, color.g, color.b, 0.3), false, 1.0)
			"orb":
				# Gradient-like orb: outer ring + filled center
				draw_circle(pos, radius * 1.3, Color(color.r, color.g, color.b, 0.2))
				draw_circle(pos, radius, color)
				draw_circle(pos, radius * 0.5, Color(1, 1, 1, 0.3))
			"field":
				draw_rect(Rect2(pos.x - radius, pos.y - radius, radius * 2, radius * 2), color)
			"constraint":
				# Line to nearest entity
				var nearest_pos: Vector2 = pos
				var nearest_dist: float = INF
				for other_id in _entities:
					if other_id == entity_id:
						continue
					var other_pos: Vector2 = _entities[other_id].get("current_pos", Vector2.ZERO)
					var dist: float = pos.distance_to(other_pos)
					if dist < nearest_dist:
						nearest_dist = dist
						nearest_pos = other_pos
				if nearest_dist < 200:
					draw_line(pos, nearest_pos, color, 2.0)
				draw_circle(pos, radius * 0.6, color)

	# Draw entity count
	var font: Font = ThemeDB.fallback_font()
	draw_string(font, Vector2(-490, -490), "Entities: %d" % _entities.size(), HORIZONTAL_ALIGNMENT_LEFT, -1, 14, Color(0.6, 0.6, 0.7))


func _process(delta: float) -> void:
	# Lerp positions toward targets
	var need_redraw: bool = false
	for entity_id in _entities:
		var e: Dictionary = _entities[entity_id]
		var current: Vector2 = e.get("current_pos", Vector2.ZERO)
		var target: Vector2 = e.get("target_pos", Vector2.ZERO)
		if current.distance_to(target) > 0.1:
			e["current_pos"] = current.lerp(target, min(1.0, LERP_SPEED * delta))
			need_redraw = true

	if need_redraw:
		queue_redraw()


func spawn_entity(entity_id: String, entity_type: String, x: float, y: float, properties: Dictionary = {}) -> void:
	var radius: float = properties.get("radius", 10.0)
	var color: Color = TYPE_COLORS.get(entity_type, Color.WHITE)
	if properties.has("color"):
		var c: Color = Color.from_string(properties["color"], color)
		color = c

	_entities[entity_id] = {
		"target_pos": Vector2(x, y),
		"current_pos": Vector2(x, y),
		"type": entity_type,
		"radius": radius,
		"color": color,
		"properties": properties,
	}
	queue_redraw()


func remove_entity(entity_id: String) -> void:
	_entities.erase(entity_id)
	queue_redraw()


func update_positions(positions: Dictionary) -> void:
	for entity_id in positions:
		if not _entities.has(entity_id):
			continue
		var pos_data: Dictionary = positions[entity_id]
		_entities[entity_id]["target_pos"] = Vector2(
			float(pos_data.get("x", 0.0)),
			float(pos_data.get("y", 0.0))
		)


func clear_all() -> void:
	_entities.clear()
	queue_redraw()


func get_entity_count() -> int:
	return _entities.size()


# Camera pan/zoom via mouse
func _input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_MIDDLE:
			_dragging = event.pressed
			_drag_start = event.position
			_camera_start = _camera.position
		elif event.button_index == MOUSE_BUTTON_WHEEL_UP:
			_camera.zoom *= 1.1
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			_camera.zoom /= 1.1
			_camera.zoom = _camera.zoom.clamp(Vector2(0.1, 0.1), Vector2(10.0, 10.0))
	elif event is InputEventMouseMotion and _dragging:
		var diff: Vector2 = (_drag_start - event.position) / _camera.zoom.x
		_camera.position = _camera_start + diff
