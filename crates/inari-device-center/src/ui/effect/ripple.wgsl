// A wall of pixel cells that carries a shock outward from wherever it was
// struck.
//
// `time`, `cell`, `strike_x`, `strike_y`, `age` and `tint` are generated from
// the fields of `Ripple`.
//
// Everything is quantised to whole cells before any distance is measured, so
// the wave advances cell by cell rather than sweeping a smooth circle across
// them. That is the whole look: a grid that conducts, not a gradient.

const GUTTER: f32 = 0.12;
const WAVELENGTH: f32 = 34.0;
const SPEED: f32 = 420.0;
const SPATIAL_FALLOFF: f32 = 0.0042;
const TEMPORAL_FALLOFF: f32 = 1.9;

fn ripple_hash(point: vec2<f32>) -> f32 {
    let scattered = fract(point * vec2<f32>(0.1031, 0.1030));
    let folded = scattered + dot(scattered, scattered.yx + 33.33);
    return fract((folded.x + folded.y) * folded.x);
}

fn effect(input: EffectInput) -> vec4<f32> {
    let edge = max(cell(input), 2.0) * input.scale;

    // The cell's centre in device pixels, which every measurement below uses so
    // that one cell carries one value.
    let index = floor(input.position / edge);
    let centre = (index + 0.5) * edge;

    // A cell is a square inset by a gutter, so the grid reads as separate
    // pixels rather than a continuous field.
    let within = fract(input.position / edge) - 0.5;
    let inside = step(abs(within.x), 0.5 - GUTTER) * step(abs(within.y), 0.5 - GUTTER);

    // Idle shimmer: each cell breathes on its own phase so the wall is alive
    // before anything strikes it.
    let phase = ripple_hash(index) * 6.2831853;
    let shimmer = 0.5 + 0.5 * sin(time(input) * 1.7 + phase);

    // The shock. `age` is negative until the wall has been struck, which keeps
    // the wave out of the first frames rather than firing one at the origin.
    var energy = 0.0;
    let elapsed = age(input);
    if elapsed >= 0.0 {
        let strike = vec2<f32>(strike_x(input), strike_y(input)) * input.scale;
        let distance = length(centre - strike);
        let front = distance - elapsed * SPEED;

        // A damped travelling wave: one cycle either side of the front, faded
        // by distance and by age so the wall settles on its own.
        let wave = cos(front / WAVELENGTH * 6.2831853)
            * exp(-abs(front) * 0.014)
            * exp(-distance * SPATIAL_FALLOFF)
            * exp(-elapsed * TEMPORAL_FALLOFF);

        // Only the leading half of the cycle lights a cell; the trailing half
        // would read as the wall dimming below its own resting level.
        energy = max(wave, 0.0);
    }

    let colour = tint(input);
    let resting = 0.06 + 0.05 * shimmer;
    let level = clamp(resting + energy * 1.6, 0.0, 1.0);

    // Mix in linear light. Mixing the encoded values darkens the midtones, and
    // this effect is almost entirely midtones.
    let lit = mix(to_linear(colour.rgb) * 0.18, to_linear(colour.rgb), level);
    return vec4<f32>(to_encoded(lit), inside * colour.a * (0.35 + 0.65 * level));
}
