// A field of pixels that blooms out from the centre and then shimmers.
//
// After Ryan Mulligan's pixel-canvas. Three things give it its texture, and all
// three are per-cell draws from one hash:
//
//   - a dot is small against its cell, so the field reads as scattered points
//     rather than as a grid of tiles;
//   - every dot has its own maximum size and its own opacity;
//   - once a dot has arrived it never stops moving, oscillating its size at its
//     own speed. That is the shimmer the effect is named for.
//
// Two things here deliberately differ from the reference, because they read
// better: the bloom starts where the pointer entered rather than always at the
// centre, and leaving unwinds in the same staggered order rather than
// collapsing the whole field at once.
//
// Every name the body calls is generated from a field of `PixelBloom`.

// The smallest a dot ever gets, as a fraction of its cell. The ceiling is
// `dot_size`, drawn per cell between this and that.
const MIN_EXTENT: f32 = 0.08;
// Seconds one dot takes to grow, before its own jitter is applied.
const DURATION: f32 = 0.22;

fn bloom_hash(point: vec2<f32>) -> f32 {
    let scattered = fract(point * vec2<f32>(0.1031, 0.1030));
    let folded = scattered + dot(scattered, scattered.yx + 33.33);
    return fract((folded.x + folded.y) * folded.x);
}

fn effect(input: EffectInput) -> vec4<f32> {
    let edge = max(gap(input), 3.0) * input.scale;

    let index = floor(input.position / edge);
    let centre = (index + 0.5) * edge;

    // Four independent draws per cell: how big it gets, how fast it shimmers,
    // how opaque it is, and which end of the palette it sits at.
    let size_seed = bloom_hash(index);
    let speed_seed = bloom_hash(index + vec2<f32>(11.7, 3.3));
    let alpha_seed = bloom_hash(index + vec2<f32>(29.1, 17.9));
    let colour_seed = bloom_hash(index + vec2<f32>(5.4, 41.2));

    let ceiling = mix(MIN_EXTENT, max(dot_size(input), MIN_EXTENT), size_seed);

    // A jittered delay, so the front of the bloom is ragged rather than a clean
    // expanding circle.
    let jitter = 0.75 + 0.5 * bloom_hash(index + vec2<f32>(2.6, 7.1));
    let origin = vec2<f32>(origin_x(input), origin_y(input)) * input.scale;
    let delay = length(centre - origin) / max(spread(input), 1.0) * jitter;
    let travel = clamp((age(input) - delay) / (DURATION * jitter), 0.0, 1.0);

    let heading = direction(input);
    var reach = 0.0;
    if heading > 0.0 {
        reach = travel;
    } else if heading < 0.0 {
        reach = 1.0 - travel;
    }
    if reach <= 0.0 {
        return vec4<f32>(0.0);
    }

    // Arrived dots oscillate between their floor and their ceiling forever, each
    // on its own phase and speed, so a settled field is never a still image.
    let beat = time(input) * shimmer(input) * (0.35 + speed_seed) + colour_seed * 6.2831853;
    let shimmering = mix(MIN_EXTENT, ceiling, 0.5 + 0.5 * sin(beat));
    let wanted = select(ceiling, shimmering, reach >= 1.0);
    let extent = wanted * reach;

    // Square dots, like the reference's `fillRect`, but with a soft shoulder so
    // a two-pixel dot does not crawl as it grows.
    let within = abs(fract(input.position / edge) - 0.5);
    let distance = max(within.x, within.y);
    let feather = 1.0 / edge;
    let core = 1.0 - smoothstep(extent - feather, extent + feather, distance);
    // The glow: a short exponential skirt outside the dot. Without it the field
    // reads as flat confetti rather than as something lit.
    let halo = exp(-max(distance - extent, 0.0) * 34.0) * glow(input);

    let colour = mix(near(input), far(input), colour_seed);
    // Per-cell opacity is what keeps the field from reading as one flat tone.
    let weight = clamp(core + halo, 0.0, 1.0) * mix(0.28, 1.0, alpha_seed);
    return vec4<f32>(colour.rgb, weight * colour.a);
}
