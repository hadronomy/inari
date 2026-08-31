export async function resolve(specifier, context, nextResolve) {
    if (specifier === "@point_of_sale/app/services/render_service") {
        return {
            shortCircuit: true,
            url: new URL("./stubs/render_service.mjs", import.meta.url).href,
        };
    }
    try {
        return await nextResolve(specifier, context);
    } catch (error) {
        if (specifier.startsWith(".") && !specifier.endsWith(".js")) {
            return nextResolve(`${specifier}.js`, context);
        }
        throw error;
    }
}
