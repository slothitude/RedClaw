# CLAW.md — Simulation Controller Directives

## Simulation Agent Instructions

You are controlling a 2D math+physics simulation world. Use the `spawn_entity`,
`set_sim_parameter`, `query_state`, and `apply_force` tools to create and
manipulate entities.

## Geometry Rules

- Position entities using Cartesian coordinates (x, y) in range [-500, 500]
- Use circular geometry for orbital arrangements
- Place entities at prime-distance intervals along 6k +/- 1 rails for node anchors
- Use logarithmic scale for sizing: radius proportional to log(mass)
- Symmetric placements produce more stable configurations

## Physics Constraints

- Pure Euler integration with velocity damping per tick
- Boundary bounce at world edges with configurable restitution
- Gravity is a constant downward acceleration (default 0, tunable)
- Forces are instantaneous impulses (force / mass = velocity change)
- No native physics server — all math is Python-side

## Entity Types

- `particle`: Simple point entity with small mass (~0.5-2.0), small radius (5-15)
- `orb`: Larger entity with medium mass (2.0-10.0), radius (15-40), rendered with gradient
- `field`: Rectangular region with transparent fill, radius defines width/height
- `constraint`: Draws lines to nearest entity within range, small radius

## Stability Targets

- Aim for stability score > 0.7 (low average velocity across entities)
- Balanced configurations: entities spread across the world, not clustered
- Orbital systems: particles orbiting an orb at the center
- Equilibrium: counterbalancing forces to keep entities in stable positions
