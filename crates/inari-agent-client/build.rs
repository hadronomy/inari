use std::{
    env, fs,
    path::{Path, PathBuf},
};

const CONTRACT: &str = "../../contracts/local-agent.openapi.json";
const CODEGEN_CONTRACT: &str = "local-agent.codegen.json";

fn main() {
    println!("cargo:rerun-if-changed={CONTRACT}");

    let contract_path = Path::new(env!("CARGO_MANIFEST_DIR")).join(CONTRACT);
    let mut contract: serde_json::Value = serde_json::from_slice(
        &fs::read(&contract_path)
            .unwrap_or_else(|error| panic!("failed to read {}: {error}", contract_path.display())),
    )
    .unwrap_or_else(|error| panic!("failed to parse {}: {error}", contract_path.display()));

    remove_schema_defaults(&mut contract);

    let destination =
        PathBuf::from(env::var_os("OUT_DIR").expect("Cargo sets OUT_DIR")).join(CODEGEN_CONTRACT);
    fs::write(&destination, serde_json::to_vec(&contract).expect("the OpenAPI contract is JSON"))
        .unwrap_or_else(|error| panic!("failed to write {}: {error}", destination.display()));
}

fn remove_schema_defaults(value: &mut serde_json::Value) {
    match value {
        serde_json::Value::Array(items) => {
            for item in items {
                remove_schema_defaults(item);
            }
        },
        serde_json::Value::Object(object) => {
            // Typify validates JSON Schema defaults more narrowly than FastAPI
            // does. Defaults remain in the committed contract; generated Rust
            // treats optional values explicitly instead of encoding them.
            object.remove("default");
            for item in object.values_mut() {
                remove_schema_defaults(item);
            }
        },
        _ => {},
    }
}
