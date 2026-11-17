#!/usr/bin/env python3
"""Strict, one-time extension of upstream Codex PR #61437 for bundled Plugin MCP cleanup.

Run ONLY after cherry-picking upstream 8afe928a into rust-v0.169.0.
This is a source patch; it does not touch any installed Codex binary or user config.
"""
from pathlib import Path
import sys


def replace_once(data: str, old: str, new: str, context: str) -> str:
    count = data.count(old)
    if count != 1:
        raise RuntimeError(f"{context}: expected exactly 1 verified anchor, found {count}")
    return data.replace(old, new, 1)


def patch_connection_manager(root: Path) -> None:
    p = root / "codex-rs/codex-mcp/src/connection_manager.rs"
    data = p.read_text(encoding="utf-8")
    old = """                matches!(server.source(), McpServerSource::Config)
                    && server.config().is_local_environment()"""
    new = """                (matches!(server.source(), McpServerSource::Config)
                    || matches!(
                        server.source(),
                        McpServerSource::Plugin(attribution)
                            | McpServerSource::SelectedPlugin(attribution)
                            if matches!(
                                (attribution.plugin_id(), server.name()),
                                ("browser@openai-bundled", "node_repl")
                                    | ("unified-computer-use@openai-bundled", "cua_repl")
                            )
                    ))
                    && server.config().is_local_environment()"""
    data = replace_once(data, old, new, "strict built-in cleanup eligibility")
    p.write_text(data, encoding="utf-8")


def patch_tests(root: Path) -> None:
    p = root / "codex-rs/codex-mcp/src/connection_manager_tests.rs"
    data = p.read_text(encoding="utf-8")
    old = """    let plugin = crate::McpServerRegistration::from_plugin(
        name.to_string(),
        crate::McpPluginAttribution::new("other@example".into(), "Other".into()),
        /*plugin_order*/ 0,
        stdio.clone(),
    );
    for (registration, expected) in [
        (configured(http), false),
        (configured(remote), false),
        (plugin, false),
        (configured(stdio), true),
    ] {"""
    new = """    let plugin = crate::McpServerRegistration::from_plugin(
        name.to_string(),
        crate::McpPluginAttribution::new("other@example".into(), "Other".into()),
        /*plugin_order*/ 0,
        stdio.clone(),
    );
    let browser = crate::McpServerRegistration::from_plugin(
        name.to_string(),
        crate::McpPluginAttribution::new(
            "browser@openai-bundled".into(),
            "Bundled Browser".into(),
        ),
        /*plugin_order*/ 0,
        stdio.clone(),
    );
    let selected_browser = crate::McpServerRegistration::from_selected_plugin(
        name.to_string(),
        crate::McpPluginAttribution::new(
            "browser@openai-bundled".into(),
            "Bundled Browser".into(),
        ),
        /*selection_order*/ 0,
        &stdio.environment_id,
        stdio.clone(),
    );
    let mismatched_cua = crate::McpServerRegistration::from_plugin(
        name.to_string(),
        crate::McpPluginAttribution::new(
            "unified-computer-use@openai-bundled".into(),
            "Bundled CUA".into(),
        ),
        /*plugin_order*/ 0,
        stdio.clone(),
    );
    let mut browser_remote = stdio.clone();
    browser_remote.environment_id = "remote".to_string();
    let remote_browser = crate::McpServerRegistration::from_plugin(
        name.to_string(),
        crate::McpPluginAttribution::new(
            "browser@openai-bundled".into(),
            "Bundled Browser".into(),
        ),
        /*plugin_order*/ 0,
        browser_remote,
    );
    for (registration, expected) in [
        (configured(http), false),
        (configured(remote), false),
        (plugin, false),
        (browser, true),
        (selected_browser, true),
        (mismatched_cua, false),
        (remote_browser, false),
        (configured(stdio), true),
    ] {"""
    data = replace_once(data, old, new, "browser registration and negative coverage")
    marker = """#[tokio::test]
async fn connection_statuses_observe_clients_without_starting_them() {"""
    extra = """#[tokio::test]
async fn bundled_cua_cleanup_skips_only_private_dormant_local_stdio() {
    let name = "cua_repl";
    let key = McpServerKey::unscoped(name);
    let mut manager = McpConnectionSet::empty(/*prefix_mcp_tool_names*/ true);
    manager.insert_test_client(name, create_ready_async_managed_client(Vec::new()).await);
    let (trigger, _receiver) = watch::channel(false);
    Arc::get_mut(
        &mut manager
            .servers
            .get_mut(&key)
            .expect("registered CUA view")
            .connection,
    )
    .expect("unique test connection")
    .startup_trigger = Some(trigger.clone());

    let stdio: McpServerConfig =
        serde_json::from_value(serde_json::json!({"command": "test-helper"}))
            .expect("valid local stdio config");
    let cua_plugin = crate::McpServerRegistration::from_plugin(
        name.to_string(),
        crate::McpPluginAttribution::new(
            "unified-computer-use@openai-bundled".into(), "Bundled CUA".into(),
        ),
        0,
        stdio.clone(),
    );
    let selected_cua_plugin = crate::McpServerRegistration::from_selected_plugin(
        name.to_string(),
        crate::McpPluginAttribution::new(
            "unified-computer-use@openai-bundled".into(), "Bundled CUA".into(),
        ),
        0,
        &stdio.environment_id,
        stdio.clone(),
    );
    let wrong_plugin = crate::McpServerRegistration::from_plugin(
        name.to_string(),
        crate::McpPluginAttribution::new("other@untrusted".into(), "Other".into()),
        0,
        stdio.clone(),
    );
    let wrong_server = crate::McpServerRegistration::from_plugin(
        name.to_string(),
        crate::McpPluginAttribution::new(
            "browser@openai-bundled".into(), "Bundled Browser".into(),
        ),
        0,
        stdio.clone(),
    );
    let mut remote = stdio.clone();
    remote.environment_id = "remote".to_string();
    let remote_plugin = crate::McpServerRegistration::from_plugin(
        name.to_string(),
        crate::McpPluginAttribution::new(
            "unified-computer-use@openai-bundled".into(), "Bundled CUA".into(),
        ),
        0,
        remote,
    );
    for (registration, expected) in [
        (cua_plugin, true),
        (selected_cua_plugin, true),
        (wrong_plugin, false),
        (wrong_server, false),
        (remote_plugin, false),
    ] {
        let mut runtime_config = crate::mcp::tests::test_mcp_config(std::env::temp_dir());
        let mut catalog = crate::ResolvedMcpCatalog::builder();
        catalog.register(registration);
        runtime_config.mcp_server_catalog = catalog.build();
        manager.config = Some(Arc::new(runtime_config));
        assert_eq!(manager.is_dormant_private_stdio(&key), expected);
    }
    trigger.send_replace(true);
    assert!(!manager.is_dormant_private_stdio(&key), "startup was requested");
}

"""
    data = replace_once(data, marker, extra + marker, "new CUA positive/negative test")
    p.write_text(data, encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: extend_bundled_cleanup.py <codex-source-root>")
    root = Path(sys.argv[1]).resolve()
    for p in ("codex-rs/codex-mcp/src/connection_manager.rs",
              "codex-rs/codex-mcp/src/connection_manager_tests.rs"):
        if not (root / p).is_file():
            raise SystemExit(f"missing source: {p}")
    patch_connection_manager(root)
    patch_tests(root)
    print("BUNDLED_PLUGIN_PATCH_APPLIED: strict official IDs, server pair, local legacy dormant private stdio")


if __name__ == "__main__":
    main()
