// A torii left standing too long: the mark cracked, pitted and bleached like
// old stone.
//
// Applied over the mark rather than instead of it. The silhouette stays the one
// the brand ships; only its surface changes, so a weathered gate is still
// recognisably the same gate — which is the point, because it is telling the
// operator that this thing is missing, not that it is something else.
//
// Everything is drawn from one value-noise field, at three scales. Cracks are
// its ridges, pits are its peaks, and the mottling is the field itself; sharing
// one source is what makes them look like features of a single surface rather
// than three textures laid over each other.

fn hash21(p: vec2<f32>) -> f32 {
    var q = fract(p * vec2<f32>(0.1031, 0.1030));
    q += dot(q, q.yx + 33.33);
    return fract((q.x + q.y) * q.x);
}

fn value_noise(p: vec2<f32>) -> f32 {
    let cell = floor(p);
    let within = fract(p);
    // Smoothstep between cells, so the field has no straight seams to give
    // away the lattice it is built on.
    let weight = within * within * (3.0 - 2.0 * within);
    let a = hash21(cell);
    let b = hash21(cell + vec2<f32>(1.0, 0.0));
    let c = hash21(cell + vec2<f32>(0.0, 1.0));
    let d = hash21(cell + vec2<f32>(1.0, 1.0));
    return mix(mix(a, b, weight.x), mix(c, d, weight.x), weight.y);
}

// Four octaves, normalised back to 0..1. Enough for stone: the fifth is below
// a pixel at the size a mark is ever drawn.
fn fbm(p: vec2<f32>) -> f32 {
    var total = 0.0;
    var amplitude = 0.5;
    var at = p;
    for (var octave = 0; octave < 4; octave++) {
        total += amplitude * value_noise(at);
        // Not exactly two, so the octaves never line their lattices up and
        // leave a visible grid.
        at *= 2.03;
        amplitude *= 0.5;
    }
    return total / 0.9375;
}

fn effect(input: EffectInput) -> vec4<f32> {
    let mark = source(input.uv);
    if mark.a <= 0.004 {
        return vec4<f32>(0.0);
    }

    let wear = clamp(amount(input), 0.0, 1.0);
    // Logical pixels, so the grain is the same size whatever the display is
    // doing. A field in uv would stretch with the mark.
    let at = input.position / max(input.scale, 0.001) * 0.22 + vec2<f32>(seed(input));

    // A crack is a line, and the ridges of a noise field are lines. Taking the
    // distance from the field's midpoint turns its slopes into ridges; the
    // threshold decides how much of each ridge survives as a crack.
    let ridge = 1.0 - abs(2.0 * fbm(at) - 1.0);
    let crack = smoothstep(0.92 - 0.14 * wear, 0.998, ridge);

    // Pitting: the surface loses bites out of it, finer than the cracks and
    // heavier the further gone the stone is.
    let pit = smoothstep(0.70 - 0.22 * wear, 0.96, fbm(at * 3.1 + vec2<f32>(17.3, 4.1)));

    // Mottling at the coarsest scale, which is what stops the result reading as
    // a flat silhouette with lines drawn on it.
    let mottle = 0.74 + 0.26 * fbm(at * 0.6 + vec2<f32>(9.7, 2.3));

    // Bleached towards the stone colour, mixed in linear light so the mid
    // tones do not sink the way an encoded mix would.
    let stone = tint(input);
    let bleached = mix(to_linear(mark.rgb), to_linear(stone.rgb), stone.a * wear);
    let colour = to_encoded(bleached * mottle);

    // A crack thins the stone; a pit takes it away entirely. Both eat further
    // as the wear rises, so one parameter carries the whole state.
    let eaten = max(crack * (0.5 + 0.5 * wear), pit * wear);
    return vec4<f32>(colour, mark.a * (1.0 - eaten));
}
