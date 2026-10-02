use std::{
    env, fs,
    path::{Path, PathBuf},
};

const CONTRACT: &str = "../../contracts/local-agent.codegen.openapi.json";
const CODEGEN_CONTRACT: &str = "local-agent.codegen.json";
const HANDWRITTEN_OPERATIONS: &[&str] = &["/v1/device-work"];

fn main() {
    println!("cargo:rerun-if-changed={CONTRACT}");

    let contract_path = Path::new(env!("CARGO_MANIFEST_DIR")).join(CONTRACT);
    let mut contract: serde_json::Value = serde_json::from_slice(
        &fs::read(&contract_path)
            .unwrap_or_else(|error| panic!("failed to read {}: {error}", contract_path.display())),
    )
    .unwrap_or_else(|error| panic!("failed to parse {}: {error}", contract_path.display()));

    remove_handwritten_operations(&mut contract);
    remove_schema_defaults(&mut contract);

    let destination =
        PathBuf::from(env::var_os("OUT_DIR").expect("Cargo sets OUT_DIR")).join(CODEGEN_CONTRACT);
    fs::write(&destination, serde_json::to_vec(&contract).expect("the OpenAPI contract is JSON"))
        .unwrap_or_else(|error| panic!("failed to write {}: {error}", destination.display()));
}

fn remove_handwritten_operations(contract: &mut serde_json::Value) {
    let Some(paths) = contract
        .get_mut("paths")
        .and_then(serde_json::Value::as_object_mut)
    else {
        return;
    };

    // Progenitor does not support multipart request bodies. Keep these
    // operations in the public contract and implement their clients by hand.
    for operation in HANDWRITTEN_OPERATIONS {
        paths.remove(*operation);
    }
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
