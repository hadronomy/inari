export async function resolve(specifier, context, nextResolve) {
    try {
        return await nextResolve(specifier, context);
    } catch (error) {
        if (specifier.startsWith(".") && !specifier.endsWith(".js")) {
            return nextResolve(`${specifier}.js`, context);
        }
        throw error;
    }
}
