// A wall of pixel cells that blooms outward from a point and stays lit.
//
// After Ryan Mulligan's pixel-canvas: each cell grows in from nothing, delayed
// by its distance from the origin, so the wall fills from one point to its
// edges. Leaving unwinds the same order, so the bloom retreats the way it came
// rather than collapsing at once.
//
// `time`, `cell`, `origin_x`, `origin_y`, `age`, `direction`, `near` and `far`
// are generated from the fields of `PixelBloom`.

// Fraction of a cell left empty around a fully grown pixel.
const GUTTER: f32 = 0.28;
// Device pixels the bloom front travels per second.
const SPREAD: f32 = 1100.0;
// Seconds one cell takes to grow, before its own jitter is applied.
const DURATION: f32 = 0.34;

fn bloom_hash(point: vec2<f32>) -> f32 {
    let scattered = fract(point * vec2<f32>(0.1031, 0.1030));
    let folded = scattered + dot(scattered, scattered.yx + 33.33);
    return fract((folded.x + folded.y) * folded.x);
}

fn effect(input: EffectInput) -> vec4<f32> {
    let edge = max(cell(input), 2.0) * input.scale;

    // Everything is measured from the cell's centre, so one cell carries one
    // value and the bloom advances cell by cell rather than sweeping a smooth
    // circle across them.
    let index = floor(input.position / edge);
    let centre = (index + 0.5) * edge;

    // Two independent draws per cell: when it moves, and what colour it is.
    let jitter = 0.75 + 0.5 * bloom_hash(index);
    let pick = bloom_hash(index + vec2<f32>(17.3, 5.1));

    let origin = vec2<f32>(origin_x(input), origin_y(input)) * input.scale;
    let delay = length(centre - origin) / SPREAD * jitter;
    let span = DURATION * jitter;

    // `direction` is +1 while the pointer is inside, -1 after it leaves, and 0
    // before the wall has ever been pointed at.
    let travel = clamp((age(input) - delay) / span, 0.0, 1.0);
    let heading = direction(input);
    var progress = 0.0;
    if heading > 0.0 {
        progress = travel;
    } else if heading < 0.0 {
        progress = 1.0 - travel;
    }
    if progress <= 0.0 {
        return vec4<f32>(0.0);
    }

    // The pixel is a square inset by the gutter, grown from its own centre.
    // Antialiased against one device pixel, or the growing edge crawls.
    let within = abs(fract(input.position / edge) - 0.5);
    let extent = (0.5 - GUTTER * 0.5) * progress;
    let feather = 1.0 / edge;
    let inside = (1.0 - smoothstep(extent - feather, extent + feather, within.x))
        * (1.0 - smoothstep(extent - feather, extent + feather, within.y));

    // A lit cell breathes on its own phase, so a settled wall is not a still
    // image.
    let breath = 0.86 + 0.14 * sin(time(input) * 1.9 + pick * 6.2831853);

    // Mixed in linear light: mixing the encoded values darkens the midtones,
    // and a palette of two close tones is almost entirely midtones.
    let colour = mix(near(input), far(input), pick);
    let lit = to_encoded(to_linear(colour.rgb) * breath);
    return vec4<f32>(lit, inside * colour.a);
}
