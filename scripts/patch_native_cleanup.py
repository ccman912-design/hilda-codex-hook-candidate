#!/usr/bin/env python3
"""Native Codex 0.169.0 bundled cleanup patch, without newer PR API backport.

Rejects unexpected source layouts. Never run in a live CODEX_HOME.
Only edits an explicit, separately cloned rust-v0.169.0 source checkout.
"""
from pathlib import Path
import re
import sys

def replace_one(s: str, old: str, new: str, label: str) -> str:
    count = s.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source anchor; found {count}")
    return s.replace(old, new, 1)

def edit(root: Path, name: str, fn):
    p = root / name
    original = p.read_text(encoding="utf-8")
    modified = fn(original)
    if modified == original:
        raise RuntimeError("no code was changed in " + name)
    p.write_text(modified, encoding="utf-8")
    print("PATCHED", name)

def mcp_call_type(s):
    return replace_one(s, "pub struct HookMcpCall {\n",
        "pub struct HookMcpCall {\n"
        "    /// Only trusted builtin bundled REPL turn_ended hooks may skip dormant startup.\n"
        "    pub skip_dormant_repl_cleanup: bool,\n", "HookMcpCall provenance flag")

def hook_runner(s):
    old = """            .execute(HookMcpCall {
                server: server.to_string(),"""
    new = """            .execute(HookMcpCall {
                skip_dormant_repl_cleanup: handler.builtin
                    && tool == "turn_ended"
                    && matches!(server, "node_repl" | "cua_repl"),
                server: server.to_string(),"""
    return replace_one(s, old, new, "builtin-only Hook runner")

def hook_core(s):
    old = """self.runtime.latest_call_tool(
                    &call.server,"""
    new = """self.runtime.latest_hook_call_tool(
                    &call.server,"""
    s=replace_one(s, old, new, "Hook core dispatch")
    return replace_one(s, "                    /*wait_for_server*/ true,\n",
        "                    call.skip_dormant_repl_cleanup,\n", "Hook core cleanup-only flag")

def connection_set(s):
    old="""    /// Rejects invalid host calls before they can start a server or capture attribution.
    pub(crate) fn validate_tool_call("""
    new="""    /// Safely classify strictly scoped bundled REPL cleanup without starting a process.
    /// None = not an eligible local private/legacy bundled server; preserve prior behavior.
    /// Some(true) = no server has ever been requested; return a no-op.
    /// Some(false) = startup was requested; use ready-only dispatch to avoid resurrection.
    pub(crate) fn bundled_repl_cleanup_mode(
        &self,
        server: &str,
        environment_id: Option<&str>,
    ) -> Option<bool> {
        let view = self.servers.get(server)?;
        if environment_id.is_some_and(|id| view.metadata.environment_id != id)
            || view.protocol_mode != McpProtocolMode::Legacy
            || view.connection.client.cancel_token.is_cancelled()
            || view.connection.client.connection_slot.is_some()
            || view.connection.legacy_fallback.is_some()
        {
            return None;
        }
        let source = self.config.as_ref()?.mcp_server_catalog.server(server)?;
        if !source.config().is_local_environment()
            || !matches!(source.config().transport, McpServerTransportConfig::Stdio { .. })
        {
            return None;
        }
        let permitted = match source.source() {
            McpServerSource::Config => matches!(server, "node_repl" | "cua_repl"),
            McpServerSource::Plugin(plugin) | McpServerSource::SelectedPlugin(plugin) => {
                matches!(
                    (plugin.plugin_id(), server),
                    ("browser@openai-bundled", "node_repl")
                        | ("unified-computer-use@openai-bundled", "cua_repl")
                )
            }
            McpServerSource::Compatibility { .. } | McpServerSource::Extension { .. } => false,
        };
        permitted.then(|| view.connection.startup_is_dormant())
    }

    /// Rejects invalid host calls before they can start a server or capture attribution.
    pub(crate) fn validate_tool_call("""
    return replace_one(s,old,new,"0.169 private legacy connection guard")

def runtime(s):
    old="""    /// Calls a tool for the host, retaining attribution while its catalogs stay unchanged.
    /// Waiting calls capture a binding after the selected server starts; non-waiting calls"""
    new="""    /// Strictly scoped builtin REPL cleanup using existing 0.169.0 MCP call APIs.
    /// Other hooks retain the original wait-for-server behavior.
    #[allow(clippy::too_many_arguments)]
    pub async fn latest_hook_call_tool(
        &self,
        server: &str,
        tool: &str,
        environment_id: Option<&str>,
        arguments: Option<serde_json::Value>,
        meta: Option<serde_json::Value>,
        requested_timeout: Option<Duration>,
        builtin_repl_cleanup: bool,
    ) -> anyhow::Result<McpToolCallResult> {
        if !builtin_repl_cleanup
            || tool != "turn_ended"
            || !matches!(server, "node_repl" | "cua_repl")
        {
            return self.latest_call_tool(
                server, tool, environment_id, arguments, meta, requested_timeout, true,
            ).await;
        }

        let published = self.current.load_full();
        published
            .connections
            .validate_tool_call(server, tool, environment_id)?;
        let mode = published
            .connections
            .bundled_repl_cleanup_mode(server, environment_id);
        match mode {
            Some(true) => Ok(McpToolCallResult {
                result: CallToolResult {
                    content: Vec::new(),
                    structured_content: None,
                    is_error: None,
                    meta: None,
                },
                source_tool_namespace: None,
            }),
            Some(false) => {
                // Non-waiting dispatch cannot initiate a new REPL even if it closes concurrently.
                self.latest_call_tool(
                    server, tool, environment_id, arguments, meta, requested_timeout, false,
                ).await
            }
            None => self.latest_call_tool(
                server, tool, environment_id, arguments, meta, requested_timeout, true,
            ).await,
        }
    }

    /// Calls a tool for the host, retaining attribution while its catalogs stay unchanged.
    /// Waiting calls capture a binding after the selected server starts; non-waiting calls"""
    return replace_one(s,old,new,"0.169 runtime native hook dispatch")

def hook_tests(s,path):
    count=s.count("HookMcpCall {")
    expected=8 if path.endswith("mod_tests.rs") else 1
    if count!=expected:
        raise RuntimeError(f"{path}: HookMcpCall literals changed: {count} != {expected}")
    s=s.replace("HookMcpCall {", "HookMcpCall {\n            skip_dormant_repl_cleanup: false,")
    if path.endswith("mod_tests.rs"):
        s=replace_one(s, "let expected_executor_call = HookMcpCall {\n            skip_dormant_repl_cleanup: false,",
            "let expected_executor_call = HookMcpCall {\n            skip_dormant_repl_cleanup: true,",
            "builtin executor positive test")
    return s

def connection_tests(s):
    marker="""#[tokio::test]
async fn connection_statuses_observe_clients_without_starting_them() {"""
    new=r'''#[tokio::test]
async fn bundled_plugin_cleanup_is_source_and_connection_bounded() {
    let mut manager = McpConnectionSet::empty(/*prefix_mcp_tool_names*/ true);
    manager.insert_test_client(
        "cua_repl", create_ready_async_managed_client(Vec::new()).await,
    );
    let (trigger, _rx) = watch::channel(false);
    Arc::get_mut(
        &mut manager.servers.get_mut("cua_repl").unwrap().connection,
    )
    .unwrap()
    .startup_trigger = Some(trigger.clone());
    let stdio: McpServerConfig =
        serde_json::from_value(serde_json::json!({"command":"synthetic-fixture"})).unwrap();
    let plugin = |id: &str, c: McpServerConfig| {
        crate::McpServerRegistration::from_plugin(
            "cua_repl".to_string(),
            crate::McpPluginAttribution::new(id.into(), "test".into()),
            0, c,
        )
    };
    let selected = crate::McpServerRegistration::from_selected_plugin(
        "cua_repl".into(),
        crate::McpPluginAttribution::new(
            "unified-computer-use@openai-bundled".into(), "test".into(),
        ),
        0, &stdio.environment_id, stdio.clone(),
    );
    let mut remote = stdio.clone();
    remote.environment_id = "remote".into();
    let mut http: McpServerConfig =
        serde_json::from_value(serde_json::json!({"url":"https://example.invalid/mcp"}))
            .unwrap();
    http.environment_id = stdio.environment_id.clone();
    let scenarios = [
        (plugin("unified-computer-use@openai-bundled", stdio.clone()), Some(true)),
        (selected, Some(true)),
        (plugin("other@untrusted", stdio.clone()), None),
        (plugin("browser@openai-bundled", stdio.clone()), None),
        (plugin("unified-computer-use@openai-bundled", remote), None),
        (plugin("unified-computer-use@openai-bundled", http), None),
        (crate::McpServerRegistration::from_config("cua_repl".into(), stdio.clone()), Some(true)),
    ];
    for (registration, expected) in scenarios {
        let mut config = crate::mcp::tests::test_mcp_config(std::env::temp_dir());
        let mut catalog = crate::ResolvedMcpCatalog::builder();
        catalog.register(registration);
        config.mcp_server_catalog = catalog.build();
        manager.config = Some(Arc::new(config));
        assert_eq!(
            manager.bundled_repl_cleanup_mode("cua_repl", None),
            expected
        );
    }
    // Keep an eligible registration for state/transitions below.
    assert_eq!(manager.bundled_repl_cleanup_mode("cua_repl", None), Some(true));
    assert_eq!(manager.bundled_repl_cleanup_mode("cua_repl", Some("wrong-environment")), None);
    manager.servers.get_mut("cua_repl").unwrap().protocol_mode =
        McpProtocolMode::V20260728;
    assert_eq!(manager.bundled_repl_cleanup_mode("cua_repl", None), None);
    manager.servers.get_mut("cua_repl").unwrap().protocol_mode =
        McpProtocolMode::Legacy;
    Arc::get_mut(
        &mut manager.servers.get_mut("cua_repl").unwrap().connection,
    )
    .unwrap()
    .client
    .connection_slot = Some(Arc::default());
    assert_eq!(manager.bundled_repl_cleanup_mode("cua_repl", None), None);
    Arc::get_mut(
        &mut manager.servers.get_mut("cua_repl").unwrap().connection,
    )
    .unwrap()
    .client
    .connection_slot = None;
    trigger.send_replace(true);
    assert_eq!(manager.bundled_repl_cleanup_mode("cua_repl", None), Some(false));
}

#[tokio::test]
async fn bundled_browser_cleanup_never_admits_unknown_plugin() {
    let mut manager = McpConnectionSet::empty(/*prefix_mcp_tool_names*/ true);
    manager.insert_test_client(
        "node_repl", create_ready_async_managed_client(Vec::new()).await,
    );
    let (trigger, _rx) = watch::channel(false);
    Arc::get_mut(
        &mut manager.servers.get_mut("node_repl").unwrap().connection,
    )
    .unwrap()
    .startup_trigger = Some(trigger);
    let stdio: McpServerConfig =
        serde_json::from_value(serde_json::json!({"command":"synthetic-fixture"})).unwrap();
    for (plugin_id, expected) in [
        ("browser@openai-bundled", Some(true)),
        ("unified-computer-use@openai-bundled", None),
        ("untrusted-plugin@example", None),
    ] {
        let mut config = crate::mcp::tests::test_mcp_config(std::env::temp_dir());
        let mut catalog = crate::ResolvedMcpCatalog::builder();
        catalog.register(crate::McpServerRegistration::from_plugin(
            "node_repl".into(),
            crate::McpPluginAttribution::new(plugin_id.into(), "test".into()),
            0, stdio.clone(),
        ));
        config.mcp_server_catalog = catalog.build();
        manager.config = Some(Arc::new(config));
        assert_eq!(manager.bundled_repl_cleanup_mode("node_repl", None), expected);
    }
}

'''
    return replace_one(s,marker,new+marker,"0.169 native source and negative tests")

def main():
    if len(sys.argv)!=2:
        raise SystemExit("usage: patch_native_cleanup.py <PINNED_0.169_SOURCE_ROOT>")
    root=Path(sys.argv[1]).resolve()
    files=[
        ("codex-rs/hooks/src/mcp.rs",mcp_call_type),
        ("codex-rs/hooks/src/engine/mcp_runner.rs",hook_runner),
        ("codex-rs/hooks/src/engine/mod_tests.rs",lambda s:hook_tests(s,"mod_tests.rs")),
        ("codex-rs/hooks/src/engine/mcp_runner_tests.rs",lambda s:hook_tests(s,"mcp_runner_tests.rs")),
        ("codex-rs/core/src/hook_mcp_executor.rs",hook_core),
        ("codex-rs/codex-mcp/src/connection_manager.rs",connection_set),
        ("codex-rs/codex-mcp/src/runtime.rs",runtime),
        ("codex-rs/codex-mcp/src/connection_manager_tests.rs",connection_tests),
    ]
    for path,_ in files:
        if not (root/path).is_file():
            raise RuntimeError("pinned source path missing: "+path)
    for path,fn in files:
        edit(root,path,fn)
    print("PATCH_SCOPE",len(files),"VERSION_NATIVE_0_169")
    print("NO_CHERRY_PICK NO_NEWER_API NO_PRIVATE_DATA")

if __name__=="__main__":
    main()
