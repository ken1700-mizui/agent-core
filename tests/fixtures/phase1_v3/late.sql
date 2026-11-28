PRAGMA application_id = 1094928945;
PRAGMA user_version = 8;
BEGIN TRANSACTION;
CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    action_hash TEXT NOT NULL,
    body TEXT NOT NULL,
    owner_mac TEXT NOT NULL,
    uses INTEGER NOT NULL CHECK (uses >= 0),
    revoked INTEGER NOT NULL CHECK (revoked IN (0, 1))
) STRICT;
CREATE TABLE audit_log (
    audit_seq INTEGER PRIMARY KEY,
    at_ms INTEGER NOT NULL CHECK (at_ms >= 0),
    action TEXT NOT NULL,
    targets TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    policy_version TEXT,
    access_epoch INTEGER,
    prev_hash TEXT,
    record_hash TEXT NOT NULL
) STRICT;
INSERT INTO "audit_log" VALUES(1,1800000000000,'POLICY_INSTALLED','{"tenant":"tnt_dev01_019e7044880000000000000000000000"}','ALLOW',NULL,'policy-1',0,NULL,'sha256:68b55d70e46e436f4f022dd65651f128910360b6ddfcf5baf8df696d3e31376d');
INSERT INTO "audit_log" VALUES(2,1800000000000,'GRANT_INSTALLED','{"run":"run_dev01_01a21fb59899d74a729ff303b981f35c"}','ALLOW',NULL,'policy-1',0,'sha256:68b55d70e46e436f4f022dd65651f128910360b6ddfcf5baf8df696d3e31376d','sha256:c59fed2a629dbc65a9549b987a4c50243442c38ce109e55a3d062dfa8ad6f330');
INSERT INTO "audit_log" VALUES(3,1800000000000,'POLICY_ALLOWED','{"attempt":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57"}','ALLOW',NULL,'policy-1',0,'sha256:c59fed2a629dbc65a9549b987a4c50243442c38ce109e55a3d062dfa8ad6f330','sha256:3b4a85bbf87f6366204a1b2af1f710b134a6c1e300cf2e2deff47b811aed5551');
INSERT INTO "audit_log" VALUES(4,1800000000000,'PERMIT_ISSUED','{"attempt":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit":"prm_dev01_019e7044880000000000000000000068"}','ALLOW',NULL,'policy-1',0,'sha256:3b4a85bbf87f6366204a1b2af1f710b134a6c1e300cf2e2deff47b811aed5551','sha256:65714c7f9cc5c97e4bfd5c2cc154c312f8476fa5d0fa7c02f560f35fe24e99e1');
INSERT INTO "audit_log" VALUES(5,1800000000000,'PERMIT_CONSUMED','{"attempt":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit":"prm_dev01_019e7044880000000000000000000068","receipt":"rcp_dev01_019e704488000000000000000000006a"}','ALLOW',NULL,'policy-1',0,'sha256:65714c7f9cc5c97e4bfd5c2cc154c312f8476fa5d0fa7c02f560f35fe24e99e1','sha256:e0e9f192302ec7843daa5d8db9d73334ecc5ae0ccd15aeecb5d0bc6c1e6d43a2');
INSERT INTO "audit_log" VALUES(6,1800000000000,'RECEIPT_CLAIMED','{"attempt":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","receipt":"rcp_dev01_019e704488000000000000000000006a"}','ALLOW',NULL,NULL,NULL,'sha256:e0e9f192302ec7843daa5d8db9d73334ecc5ae0ccd15aeecb5d0bc6c1e6d43a2','sha256:42ef872d610318fa0501502909f968468638e46b981184b3e8b86ab155617bdc');
INSERT INTO "audit_log" VALUES(7,1800000000000,'DISPATCH_STARTED','{"attempt":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","receipt":"rcp_dev01_019e704488000000000000000000006a"}','ALLOW',NULL,NULL,NULL,'sha256:42ef872d610318fa0501502909f968468638e46b981184b3e8b86ab155617bdc','sha256:66bce22d06e94c9c19a081f2cedfddb0d98271afb4c6edb37c75ec0506e95816');
CREATE TABLE budget_blocks (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    reservation_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (scope_type, scope_id, reservation_id, reason)
) STRICT;
CREATE TABLE budget_caps (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    cap_micro INTEGER NOT NULL CHECK (cap_micro >= 0),
    PRIMARY KEY (scope_type, scope_id)
) STRICT;
INSERT INTO "budget_caps" VALUES('STEP','stp_dev01_01a21fb5989deb71f277e352ca617965',100000);
INSERT INTO "budget_caps" VALUES('RUN','run_dev01_01a21fb59899d74a729ff303b981f35c',100000);
INSERT INTO "budget_caps" VALUES('TASK','tsk_dev01_01a21fb598970033d58ab19ebafa0a12',100000);
INSERT INTO "budget_caps" VALUES('USER','usr_dev01_019e7044880000000000000000000001',100000);
INSERT INTO "budget_caps" VALUES('TENANT','tnt_dev01_019e7044880000000000000000000000',100000);
INSERT INTO "budget_caps" VALUES('PERIOD','synthetic-2026-11',100000);
CREATE TABLE counter_caps (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    counter TEXT NOT NULL,
    cap INTEGER NOT NULL CHECK (cap >= 0),
    PRIMARY KEY (scope_type, scope_id, counter)
) STRICT;
INSERT INTO "counter_caps" VALUES('RUN','run_dev01_01a21fb59899d74a729ff303b981f35c','MODEL_CALLS',100);
INSERT INTO "counter_caps" VALUES('TASK','tsk_dev01_01a21fb598970033d58ab19ebafa0a12','MODEL_CALLS',100);
INSERT INTO "counter_caps" VALUES('RUN','run_dev01_01a21fb59899d74a729ff303b981f35c','TOOL_CALLS',100);
INSERT INTO "counter_caps" VALUES('TASK','tsk_dev01_01a21fb598970033d58ab19ebafa0a12','TOOL_CALLS',100);
INSERT INTO "counter_caps" VALUES('RUN','run_dev01_01a21fb59899d74a729ff303b981f35c','RETRIES',100);
INSERT INTO "counter_caps" VALUES('TASK','tsk_dev01_01a21fb598970033d58ab19ebafa0a12','RETRIES',100);
INSERT INTO "counter_caps" VALUES('RUN','run_dev01_01a21fb59899d74a729ff303b981f35c','WORK_UNITS',100);
INSERT INTO "counter_caps" VALUES('TASK','tsk_dev01_01a21fb598970033d58ab19ebafa0a12','WORK_UNITS',100);
CREATE TABLE counter_usage (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    counter TEXT NOT NULL,
    used INTEGER NOT NULL CHECK (used >= 0),
    PRIMARY KEY (scope_type, scope_id, counter)
) STRICT;
INSERT INTO "counter_usage" VALUES('RUN','run_dev01_01a21fb59899d74a729ff303b981f35c','TOOL_CALLS',1);
INSERT INTO "counter_usage" VALUES('TASK','tsk_dev01_01a21fb598970033d58ab19ebafa0a12','TOOL_CALLS',1);
INSERT INTO "counter_usage" VALUES('RUN','run_dev01_01a21fb59899d74a729ff303b981f35c','WORK_UNITS',1);
INSERT INTO "counter_usage" VALUES('TASK','tsk_dev01_01a21fb598970033d58ab19ebafa0a12','WORK_UNITS',1);
CREATE TABLE decisions (
    attempt_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    action_hash TEXT NOT NULL,
    reservation_id TEXT NOT NULL,
    descriptor TEXT NOT NULL,
    registration TEXT,
    decision_ref TEXT NOT NULL,
    approval_required INTEGER NOT NULL CHECK (approval_required IN (0, 1)),
    approval_id TEXT
) STRICT;
INSERT INTO "decisions" VALUES('att_dev01_01a21fb598a148da4f8d78e17cd7ae57','tnt_dev01_019e7044880000000000000000000000','run_dev01_01a21fb59899d74a729ff303b981f35c','sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e','rsv_dev01_019e7044880000000000000000000066','{"access_epoch":0,"apply_change":null,"args_hash":"sha256:0c7cfd7dedb9dd04fd7e05c19a28f4b346e05ea79ad9e440a2e524d5b7082bc0","attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","cost_basis":{"estimate":{"currency":"XTU","micro_units":0},"kind":"tool"},"cost_cap":{"currency":"XTU","micro_units":5000},"descriptor_version":3,"destinations":[],"effect_class":"READ_ONLY","expires_at_ms":1800000060000,"hash_scheme":"SYNTHETIC_SHA256","kind":"TOOL_CALL","model":null,"policy_version":"policy-1","request_hash":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000066","run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","sandbox_profile_hash":null,"step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965","target_resources":[],"task_id":"tsk_dev01_01a21fb598970033d58ab19ebafa0a12","tenant_id":"tnt_dev01_019e7044880000000000000000000000","tool":{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:09cc39268f898a09466880756b2f7afd81fe09a49c7114edd5210974200db5b1","tool_id":"synthetic_op","version":"1.0.0"},"workspace_ref":"sha256:20c988542b445212f959e5f1094801ce10e7cae1e7832c218e8941e3bbb84b18"}','{"approval_ref":"sha256:e62ef87580cf4336a581d63305583a27edc96c4a48f2f5bffec9d238981714ce","artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest":{"approval_conditions":[],"cost_estimate":{"currency":"XTU","micro_units":0},"destinations":[],"effect_class":"READ_ONLY","external_guarantee":"NONE","input_schema":{"fields":[{"apply":null,"max_length":256,"maximum":null,"minimum":null,"name":"value","required":true,"resource":null,"type":"STRING"}],"max_bytes":1024},"kind":"BUILTIN","manifest_hash":"sha256:09cc39268f898a09466880756b2f7afd81fe09a49c7114edd5210974200db5b1","max_output_bytes":70000,"output_schema":{"fields":[{"apply":null,"max_length":65536,"maximum":null,"minimum":null,"name":"content","required":true,"resource":null,"type":"STRING"},{"apply":null,"max_length":null,"maximum":null,"minimum":null,"name":"truncated","required":true,"resource":null,"type":"BOOLEAN"}],"max_bytes":70000},"required_permissions":[],"resources":[],"sandbox_profile":null,"timeout_ms":5000,"tool_id":"synthetic_op","version":"1.0.0"}}','sha256:98ade79cecad86896699cd877026652a6d20fdad6223e161923f2856d3b06388',0,NULL);
CREATE TABLE deny_list (
    tenant_id TEXT NOT NULL,
    path TEXT NOT NULL,
    PRIMARY KEY (tenant_id, path)
) STRICT;
CREATE TABLE entity_times (
    entity_id TEXT PRIMARY KEY,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT;
INSERT INTO "entity_times" VALUES('tsk_dev01_01a21fb598970033d58ab19ebafa0a12',1800000000000);
INSERT INTO "entity_times" VALUES('run_dev01_01a21fb59899d74a729ff303b981f35c',1800000000000);
CREATE TABLE events (
    tenant_id TEXT NOT NULL
        REFERENCES tenant_head (tenant_id) DEFERRABLE INITIALLY DEFERRED,
    tenant_seq INTEGER NOT NULL CHECK (tenant_seq >= 1),
    event_id TEXT NOT NULL,
    run_id TEXT REFERENCES run_head (run_id) DEFERRABLE INITIALLY DEFERRED,
    run_seq INTEGER CHECK (run_seq >= 1),
    hash_version INTEGER NOT NULL,
    event_hash TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (tenant_id, tenant_seq),
    CONSTRAINT events_event_id_unique UNIQUE (event_id),
    CONSTRAINT events_run_position_unique UNIQUE (run_id, run_seq),
    CHECK ((run_id IS NULL) = (run_seq IS NULL))
) STRICT;
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',1,'evt_dev01_01a21fb598980917b0c46747eb74d11a',NULL,NULL,1,'sha256:4b8b2b0c405153a38abab3b75f6e234bf55619d0559af9a521cdac819dc976b2','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598980917b0c46747eb74d11a","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"CREATE","after":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":[],"status":"CREATED","task_id":"tsk_dev01_01a21fb598970033d58ab19ebafa0a12"},"before":null,"entity":"task"}]},"policy_version":"policy-1","prev_hash":null,"recorded_at_ms":1795828324504,"refs":[],"revocation_gen":0,"run_id":null,"run_seq":null,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":1,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',2,'evt_dev01_01a21fb59899aabdaa20520986c95ee7','run_dev01_01a21fb59899d74a729ff303b981f35c',1,1,'sha256:6aa397f2995b05ab5fa44db9ace315eb6b20ce15489746c34851a849761fec08','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb59899aabdaa20520986c95ee7","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":[],"status":"ACTIVE","task_id":"tsk_dev01_01a21fb598970033d58ab19ebafa0a12"},"before":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":[],"status":"CREATED","task_id":"tsk_dev01_01a21fb598970033d58ab19ebafa0a12"},"entity":"task"},{"action":"CREATE","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000063","run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","status":"CREATED","task_id":"tsk_dev01_01a21fb598970033d58ab19ebafa0a12"},"before":null,"entity":"run","evidence":null},{"action":"ATTACH_RUN","after":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":["run_dev01_01a21fb59899d74a729ff303b981f35c"],"status":"ACTIVE","task_id":"tsk_dev01_01a21fb598970033d58ab19ebafa0a12"},"before":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":[],"status":"ACTIVE","task_id":"tsk_dev01_01a21fb598970033d58ab19ebafa0a12"},"entity":"task"}]},"policy_version":"policy-1","prev_hash":"sha256:4b8b2b0c405153a38abab3b75f6e234bf55619d0559af9a521cdac819dc976b2","recorded_at_ms":1795828324505,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":1,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":2,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',3,'evt_dev01_01a21fb5989a7510afed223d3bed15cf','run_dev01_01a21fb59899d74a729ff303b981f35c',2,1,'sha256:364c1a1c735f98ebf21abe59f3ef3a89cfb0c8ede4dd14359b0ab15c7888a167','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5989a7510afed223d3bed15cf","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000063","run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","status":"QUEUED","task_id":"tsk_dev01_01a21fb598970033d58ab19ebafa0a12"},"before":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000063","run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","status":"CREATED","task_id":"tsk_dev01_01a21fb598970033d58ab19ebafa0a12"},"entity":"run","evidence":null}]},"policy_version":"policy-1","prev_hash":"sha256:6aa397f2995b05ab5fa44db9ace315eb6b20ce15489746c34851a849761fec08","recorded_at_ms":1795828324506,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":2,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":3,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',4,'evt_dev01_01a21fb5989c500dc1237442db76c9dd','run_dev01_01a21fb59899d74a729ff303b981f35c',3,1,'sha256:1d882cd3889b927027b90a07eb9e5d9c350cc333803d11b1f6dd13ae2d056450','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5989c500dc1237442db76c9dd","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000063","run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","status":"RUNNING","task_id":"tsk_dev01_01a21fb598970033d58ab19ebafa0a12"},"before":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000063","run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","status":"QUEUED","task_id":"tsk_dev01_01a21fb598970033d58ab19ebafa0a12"},"entity":"run","evidence":null}]},"policy_version":"policy-1","prev_hash":"sha256:364c1a1c735f98ebf21abe59f3ef3a89cfb0c8ede4dd14359b0ab15c7888a167","recorded_at_ms":1795828324508,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":3,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":4,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',5,'evt_dev01_01a21fb5989dbda14572086281a1f432','run_dev01_01a21fb59899d74a729ff303b981f35c',4,1,'sha256:c59a10b26cf9e12477f1d6cdd014be33b466d1f095b35a4736ee80485a8659a9','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5989dbda14572086281a1f432","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"CREATE","after":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":4,"status":"PLANNED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"before":null,"entity":"step"}]},"policy_version":"policy-1","prev_hash":"sha256:1d882cd3889b927027b90a07eb9e5d9c350cc333803d11b1f6dd13ae2d056450","recorded_at_ms":1795828324510,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":4,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":5,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',6,'evt_dev01_01a21fb5989f58f90fb82eb6b2f566a4','run_dev01_01a21fb59899d74a729ff303b981f35c',5,1,'sha256:1d6404f636480a18f9cae18a81b1cfd5ef13b48c8496351a12501c88d4dfa7d6','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5989f58f90fb82eb6b2f566a4","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":4,"status":"IN_PROGRESS","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"before":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":4,"status":"PLANNED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"entity":"step"}]},"policy_version":"policy-1","prev_hash":"sha256:c59a10b26cf9e12477f1d6cdd014be33b466d1f095b35a4736ee80485a8659a9","recorded_at_ms":1795828324511,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":5,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":6,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',7,'evt_dev01_01a21fb598a1db1ccfdf22e265ab88af','run_dev01_01a21fb59899d74a729ff303b981f35c',6,1,'sha256:e3e4d67672a364384a02e3600b23976a48a67c97f7a7be072df073f4fdc74e49','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598a1db1ccfdf22e265ab88af","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"CREATE","after":{"action_hash":null,"approval_required":null,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":null,"receipt_id":null,"reservation_id":null,"state":"PROPOSED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"before":null,"entity":"attempt","evidence":null,"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:1d6404f636480a18f9cae18a81b1cfd5ef13b48c8496351a12501c88d4dfa7d6","recorded_at_ms":1795828324513,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":6,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":7,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',8,'evt_dev01_01a21fb598a3e83b9d2e26daac3100c2','run_dev01_01a21fb59899d74a729ff303b981f35c',7,1,'sha256:77efc07ecc5ae4354c297101c3fe9a627f23a8c08787c1b590ea1b588dd1ffae','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598a3e83b9d2e26daac3100c2","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":null,"approval_required":null,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":null,"receipt_id":null,"reservation_id":null,"state":"VALIDATED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"before":{"action_hash":null,"approval_required":null,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":null,"receipt_id":null,"reservation_id":null,"state":"PROPOSED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"entity":"attempt","evidence":{"kind":"validation","schema_ref":"sha256:df0ad6e43880f09c90ebf95f19110178aba6890df0010ebda7485029e2b543b4"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:e3e4d67672a364384a02e3600b23976a48a67c97f7a7be072df073f4fdc74e49","recorded_at_ms":1795828324515,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":7,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":8,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',9,'evt_dev01_01a21fb598a52b081c8fe423fa631dde','run_dev01_01a21fb59899d74a729ff303b981f35c',8,1,'sha256:3ed8ce53da27de6ddf55282c73509774720daa3ca913237fda2134007399eeb5','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598a52b081c8fe423fa631dde","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":null,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"CONTENT_FIXED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"before":{"action_hash":null,"approval_required":null,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":null,"receipt_id":null,"reservation_id":null,"state":"VALIDATED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"entity":"attempt","evidence":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","kind":"content_fixed","reservation_id":"rsv_dev01_019e7044880000000000000000000066"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:77efc07ecc5ae4354c297101c3fe9a627f23a8c08787c1b590ea1b588dd1ffae","recorded_at_ms":1795828324517,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":8,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":9,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',10,'evt_dev01_01a21fb598a952e9c7b3dac2c9f45e50','run_dev01_01a21fb59899d74a729ff303b981f35c',9,1,'sha256:818e32d3608b004f1fc5ab968a376c3537de0dbc4304f5ab48d2cd85b1fdaf7c','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598a952e9c7b3dac2c9f45e50","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"POLICY_ALLOWED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"before":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":null,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"CONTENT_FIXED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"entity":"attempt","evidence":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"decision_ref":"sha256:86ae35d58a6aa3b5742df94ef9d7162219f0106a911ae1954c1f0604aaec805d","kind":"policy_decision","policy_version":"policy-1"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:3ed8ce53da27de6ddf55282c73509774720daa3ca913237fda2134007399eeb5","recorded_at_ms":1795828324521,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":9,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":10,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',11,'evt_dev01_01a21fb598acb7de855f42ae3829932a','run_dev01_01a21fb59899d74a729ff303b981f35c',10,1,'sha256:5b879b2e738f66c51f5ef053145abcb8a83dc6a97e3c57d193d8cadb9475a689','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598acb7de855f42ae3829932a","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"BUDGET_RESERVED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"before":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"POLICY_ALLOWED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"entity":"attempt","evidence":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","kind":"reservation","reservation_id":"rsv_dev01_019e7044880000000000000000000066"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:818e32d3608b004f1fc5ab968a376c3537de0dbc4304f5ab48d2cd85b1fdaf7c","recorded_at_ms":1795828324524,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":10,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":11,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',12,'evt_dev01_01a21fb598aee75ca3e6c26f16f682d9','run_dev01_01a21fb59899d74a729ff303b981f35c',11,1,'sha256:ccd9e2ed02e089eaa9e11cf790271546d8b550368bd64baa7ae5688e1689d00f','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598aee75ca3e6c26f16f682d9","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"INTENT_RECORDED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"before":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"BUDGET_RESERVED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"entity":"attempt","evidence":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","intent_ref":"sha256:282bcbc3f0a34a8a4ac6f00c276fcf66cf3757a3332e83d92208e5079af46922","kind":"intent"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:5b879b2e738f66c51f5ef053145abcb8a83dc6a97e3c57d193d8cadb9475a689","recorded_at_ms":1795828324526,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":11,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":12,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',13,'evt_dev01_01a21fb598b03e762fd9bd0edc1ae3f1','run_dev01_01a21fb59899d74a729ff303b981f35c',12,1,'sha256:a504bc8ba9846222ca8fa3558c5e07fbf0bc482d390fa5369f78dbcaa2213e32','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598b03e762fd9bd0edc1ae3f1","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":"prm_dev01_019e7044880000000000000000000068","receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"PERMIT_ISSUED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"before":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"INTENT_RECORDED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"entity":"attempt","evidence":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","kind":"permit_issued","permit_id":"prm_dev01_019e7044880000000000000000000068","reservation_id":"rsv_dev01_019e7044880000000000000000000066"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:ccd9e2ed02e089eaa9e11cf790271546d8b550368bd64baa7ae5688e1689d00f","recorded_at_ms":1795828324528,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":12,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":13,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',14,'evt_dev01_01a21fb598b49aa1086f4c11f0843216','run_dev01_01a21fb59899d74a729ff303b981f35c',13,1,'sha256:fddab710711518ad4c0c17c82d3694847162fe7c8567b6cdaef7fa6908481b76','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598b49aa1086f4c11f0843216","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":"prm_dev01_019e7044880000000000000000000068","receipt_id":"rcp_dev01_019e704488000000000000000000006a","reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"PERMIT_CONSUMED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"before":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":"prm_dev01_019e7044880000000000000000000068","receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"PERMIT_ISSUED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"entity":"attempt","evidence":{"kind":"permit_consumed","permit_id":"prm_dev01_019e7044880000000000000000000068","receipt_id":"rcp_dev01_019e704488000000000000000000006a"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:a504bc8ba9846222ca8fa3558c5e07fbf0bc482d390fa5369f78dbcaa2213e32","recorded_at_ms":1795828324532,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":13,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":14,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',15,'evt_dev01_01a21fb598b76b17b3e66bf7c337a576','run_dev01_01a21fb59899d74a729ff303b981f35c',14,1,'sha256:9e2a038a251349c6d3e4d26a7d6ee24dfc366329bd82db7cd5d597874f94a889','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598b76b17b3e66bf7c337a576","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":"prm_dev01_019e7044880000000000000000000068","receipt_id":"rcp_dev01_019e704488000000000000000000006a","reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"DISPATCH_AUTHORIZED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"before":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":"prm_dev01_019e7044880000000000000000000068","receipt_id":"rcp_dev01_019e704488000000000000000000006a","reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"PERMIT_CONSUMED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"entity":"attempt","evidence":{"kind":"dispatch","receipt_id":"rcp_dev01_019e704488000000000000000000006a","report_ref":"sha256:f597b632fea66fac6a17c94422e52a3162efa1b1a935ead0eaa53f09beb77f9e"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:fddab710711518ad4c0c17c82d3694847162fe7c8567b6cdaef7fa6908481b76","recorded_at_ms":1795828324535,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":14,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":15,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',16,'evt_dev01_01a21fb598b92acd2e8e7c2338f314d3','run_dev01_01a21fb59899d74a729ff303b981f35c',15,1,'sha256:f354e870fc0b870e7e0cf5b675f863186e73211123b702683d80a98220f49fdb','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598b92acd2e8e7c2338f314d3","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":"prm_dev01_019e7044880000000000000000000068","receipt_id":"rcp_dev01_019e704488000000000000000000006a","reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"DISPATCH_STARTED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"before":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":"prm_dev01_019e7044880000000000000000000068","receipt_id":"rcp_dev01_019e704488000000000000000000006a","reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"DISPATCH_AUTHORIZED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"entity":"attempt","evidence":{"kind":"dispatch","receipt_id":"rcp_dev01_019e704488000000000000000000006a","report_ref":"sha256:64ef6525f8d28eac61665bbf1ab90ac1dcfdccd8de474827e4fa55ff77b780c0"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:9e2a038a251349c6d3e4d26a7d6ee24dfc366329bd82db7cd5d597874f94a889","recorded_at_ms":1795828324537,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":15,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":16,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',17,'evt_dev01_01a21fb598cdf5f6df2517978916899c','run_dev01_01a21fb59899d74a729ff303b981f35c',16,1,'sha256:48ce465e8ef6e33f2ab02d04635e2c6457da16c2e09c2e0cf9778eff18708afa','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598cdf5f6df2517978916899c","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000063","run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","status":"RECOVERING","task_id":"tsk_dev01_01a21fb598970033d58ab19ebafa0a12"},"before":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000063","run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","status":"RUNNING","task_id":"tsk_dev01_01a21fb598970033d58ab19ebafa0a12"},"entity":"run","evidence":null}]},"policy_version":"policy-1","prev_hash":"sha256:f354e870fc0b870e7e0cf5b675f863186e73211123b702683d80a98220f49fdb","recorded_at_ms":1795828324557,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":16,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":17,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',18,'evt_dev01_01a21fb598cff3a7438d7300f4f9249f','run_dev01_01a21fb59899d74a729ff303b981f35c',17,1,'sha256:a72e83e6a92432db788bedc52cd403624ae118899d042c713320ecc6317a67c7','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598cff3a7438d7300f4f9249f","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":"prm_dev01_019e7044880000000000000000000068","receipt_id":"rcp_dev01_019e704488000000000000000000006a","reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"OUTCOME_UNKNOWN","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"before":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":"prm_dev01_019e7044880000000000000000000068","receipt_id":"rcp_dev01_019e704488000000000000000000006a","reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"DISPATCH_STARTED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"entity":"attempt","evidence":{"kind":"outcome","receipt_id":"rcp_dev01_019e704488000000000000000000006a","report_ref":"sha256:291716c8197493161e42ad60f073a0246bec9e02bc4f6fc0c21923f0d9800180"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:48ce465e8ef6e33f2ab02d04635e2c6457da16c2e09c2e0cf9778eff18708afa","recorded_at_ms":1795828324559,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":17,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":18,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',19,'evt_dev01_01a21fb598d108c5cf022a82003d23cd','run_dev01_01a21fb59899d74a729ff303b981f35c',18,1,'sha256:c91f2b425656c200f2aefb74a4f756c164cbbebc79e11d1a9659cc0e4b87548a','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598d108c5cf022a82003d23cd","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000063","run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","status":"RECONCILIATION_REQUIRED","task_id":"tsk_dev01_01a21fb598970033d58ab19ebafa0a12"},"before":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000063","run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","status":"RECOVERING","task_id":"tsk_dev01_01a21fb598970033d58ab19ebafa0a12"},"entity":"run","evidence":{"evidence_ref":"sha256:9cfe9c2c9457dd0b9dbf2f658dcf711f57716b3755e801aa3ae96ac7bf53c2c4","kind":"reconciliation_trigger","reason":"OUTCOME_UNKNOWN"}}]},"policy_version":"policy-1","prev_hash":"sha256:a72e83e6a92432db788bedc52cd403624ae118899d042c713320ecc6317a67c7","recorded_at_ms":1795828324561,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":18,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":19,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',20,'evt_dev01_01a21fb598d3e546fe40a307cb17d466','run_dev01_01a21fb59899d74a729ff303b981f35c',19,1,'sha256:3b4f6663672e6453305026b74e5a8dd3cbbe196cbe39c9ae53fd59c809d95bab','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598d3e546fe40a307cb17d466","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":4,"status":"RECONCILIATION_REQUIRED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"before":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":4,"status":"IN_PROGRESS","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"entity":"step"}]},"policy_version":"policy-1","prev_hash":"sha256:c91f2b425656c200f2aefb74a4f756c164cbbebc79e11d1a9659cc0e4b87548a","recorded_at_ms":1795828324563,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":19,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":20,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',21,'evt_dev01_01a21fb598d5a96ad3dc2e5f9d0ad4d3','run_dev01_01a21fb59899d74a729ff303b981f35c',20,1,'sha256:b6193143e3ee6f9512706a04ab0bcc3ce53a017b4de7fb40e70d300c2e73d3c8','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598d5a96ad3dc2e5f9d0ad4d3","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":"prm_dev01_019e7044880000000000000000000068","receipt_id":"rcp_dev01_019e704488000000000000000000006a","reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"COMPLETED","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"before":{"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","approval_required":false,"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","permit_id":"prm_dev01_019e7044880000000000000000000068","receipt_id":"rcp_dev01_019e704488000000000000000000006a","reservation_id":"rsv_dev01_019e7044880000000000000000000066","state":"OUTCOME_UNKNOWN","step_id":"stp_dev01_01a21fb5989deb71f277e352ca617965"},"entity":"attempt","evidence":{"evidence_ref":"sha256:08ddfb94e3ee4430f913f008caa019f5ba37fb8563eea74d8613971954921eb1","kind":"reconciliation","outcome":"COMPLETED"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:3b4f6663672e6453305026b74e5a8dd3cbbe196cbe39c9ae53fd59c809d95bab","recorded_at_ms":1795828324565,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","run_seq":20,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":21,"type":"runtime.transition"}');
CREATE TABLE intents (
    attempt_id TEXT PRIMARY KEY,
    action_hash TEXT NOT NULL,
    reservation_id TEXT NOT NULL,
    intent_ref TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms >= 0)
) STRICT;
INSERT INTO "intents" VALUES('att_dev01_01a21fb598a148da4f8d78e17cd7ae57','sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e','rsv_dev01_019e7044880000000000000000000066','sha256:282bcbc3f0a34a8a4ac6f00c276fcf66cf3757a3332e83d92208e5079af46922',1800000000000);
CREATE TABLE ledger_clock (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    high_water_ms INTEGER NOT NULL CHECK (high_water_ms >= 0)
) STRICT;
INSERT INTO "ledger_clock" VALUES(1,1800000000000);
CREATE TABLE ledger_entries (
    entry_seq INTEGER PRIMARY KEY,
    reservation_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT,
    hold_micro INTEGER NOT NULL CHECK (hold_micro >= 0),
    spent_micro INTEGER NOT NULL CHECK (spent_micro >= 0),
    delta_micro INTEGER NOT NULL CHECK (delta_micro >= 0),
    usage_event_id TEXT,
    refers_to INTEGER,
    evidence_ref TEXT,
    at_ms INTEGER NOT NULL CHECK (at_ms >= 0),
    CONSTRAINT ledger_usage_once UNIQUE (reservation_id, usage_event_id)
) STRICT;
INSERT INTO "ledger_entries" VALUES(1,'rsv_dev01_019e7044880000000000000000000066','STATE','ID_ALLOCATED',0,0,0,NULL,NULL,NULL,1800000000000);
INSERT INTO "ledger_entries" VALUES(2,'rsv_dev01_019e7044880000000000000000000066','STATE','RESERVED',5000,0,0,NULL,NULL,NULL,1800000000000);
INSERT INTO "ledger_entries" VALUES(3,'rsv_dev01_019e7044880000000000000000000066','STATE','CONSUMED',5000,0,0,NULL,NULL,NULL,1800000000000);
INSERT INTO "ledger_entries" VALUES(4,'rsv_dev01_019e7044880000000000000000000066','STATE','ACTIVE',5000,0,0,NULL,NULL,NULL,1800000000000);
INSERT INTO "ledger_entries" VALUES(5,'rsv_dev01_019e7044880000000000000000000066','STATE','UNKNOWN',5000,0,0,NULL,NULL,NULL,1800000000000);
INSERT INTO "ledger_entries" VALUES(6,'rsv_dev01_019e7044880000000000000000000066','STATE','RECONCILED',0,0,0,NULL,NULL,NULL,1800000000000);
INSERT INTO "ledger_entries" VALUES(7,'rsv_dev01_019e7044880000000000000000000066','RECONCILIATION',NULL,0,0,0,NULL,NULL,'sha256:08ddfb94e3ee4430f913f008caa019f5ba37fb8563eea74d8613971954921eb1',1800000000000);
CREATE TABLE not_dispatched (
    receipt_id TEXT PRIMARY KEY,
    report_ref TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms >= 0)
) STRICT;
CREATE TABLE operation_results (
    attempt_id TEXT PRIMARY KEY,
    result_digest TEXT NOT NULL
) STRICT;
CREATE TABLE owner_sequence (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    last_seq INTEGER NOT NULL CHECK (last_seq >= 0)
) STRICT;
INSERT INTO "owner_sequence" VALUES(1,3);
CREATE TABLE permits (
    permit_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    body TEXT NOT NULL,
    mac TEXT NOT NULL,
    state TEXT NOT NULL
) STRICT;
INSERT INTO "permits" VALUES('prm_dev01_019e7044880000000000000000000068','att_dev01_01a21fb598a148da4f8d78e17cd7ae57','tnt_dev01_019e7044880000000000000000000000','run_dev01_01a21fb59899d74a729ff303b981f35c','{"access_epoch":0,"action_hash":"sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e","attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","consumer":"BROKER","expires_at_ms":1800000030000,"issued_at_ms":1800000000000,"owner_epoch":1,"permit_id":"prm_dev01_019e7044880000000000000000000068","permit_version":1,"policy_version":"policy-1","reservation_id":"rsv_dev01_019e7044880000000000000000000066","revocation_gen":0,"run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","tenant_id":"tnt_dev01_019e7044880000000000000000000000"}','6d995b3a8e3a7f4fbe2fd63838f91481e491135d1f2b944ec9bcc65b99208502','CONSUMED');
CREATE TABLE policies (
    tenant_id TEXT PRIMARY KEY,
    policy_version TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    body TEXT NOT NULL
) STRICT;
INSERT INTO "policies" VALUES('tnt_dev01_019e7044880000000000000000000000','policy-1','sha256:594ce50cbd87ebf72d17dcaea5c070fe290b7cd11492c55618a231783e03a030','{"allowed_effects":["READ_ONLY","IRREVERSIBLE","MODEL_CALL"],"approval_effects":["IRREVERSIBLE"],"approval_tools":[],"deny_resources":["workspace/secret/**"],"max_approval_ttl_ms":600000,"max_permit_ttl_ms":60000,"model_providers":["FAKE","REPLAY"],"policy_version":"policy-1","price_table_id":"test_virtual","price_table_version":"1.0.0","tenant_id":"tnt_dev01_019e7044880000000000000000000000","tools":[{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:0482401686134e9d52335e602be40b62a225b6c59e8f602fbcedea9456ef337f","tool_id":"costly_read","version":"1.0.0"},{"artifact_hash":"sha256:bca87f5d1d6d150cea3b0b581ac63b25033c9c7d85098bb389bd40b9fc8303c3","manifest_hash":"sha256:db941c7739d1e3a22e9e552e804d30ff2965905bf9de10c58c5efd0394250906","tool_id":"read_file","version":"1.0.0"},{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:60dda8fbcad88900228c8c3e0a15b1de11eafe88baded8708d43b0f0b0ef0933","tool_id":"synthetic_change","version":"1.0.0"},{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:aa2f09d596c1d1b35271252900340d3a2fb9916cc84ba6530b5d9c9b46861823","tool_id":"synthetic_costly","version":"1.0.0"},{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:09cc39268f898a09466880756b2f7afd81fe09a49c7114edd5210974200db5b1","tool_id":"synthetic_op","version":"1.0.0"},{"artifact_hash":"sha256:bca87f5d1d6d150cea3b0b581ac63b25033c9c7d85098bb389bd40b9fc8303c3","manifest_hash":"sha256:9f171e9b16b75b7f2b2d920528d35dd8bd9ca4f247d6518b864a5cc35c3b32a6","tool_id":"write_file","version":"1.0.0"}],"trusted_synthetic_tasks":["sha256:18827c6386e82a4172ab53e2c6f913e3b56922f1773400afa220282b0db30e62"],"trusted_synthetic_workspaces":["sha256:20c988542b445212f959e5f1094801ce10e7cae1e7832c218e8941e3bbb84b18"]}');
CREATE TABLE price_tables (
    price_table_id TEXT NOT NULL,
    version TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (price_table_id, version)
) STRICT;
INSERT INTO "price_tables" VALUES('test_virtual','1.0.0','{"final_usage_is_complete":true,"items":[["scripted-model-small",{"input_micro_per_mtok":1000000,"output_micro_per_mtok":4000000}]],"price_table_id":"test_virtual","status":"test","version":"1.0.0"}');
CREATE TABLE receipts (
    receipt_id TEXT PRIMARY KEY,
    permit_id TEXT NOT NULL UNIQUE,
    attempt_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    body TEXT NOT NULL,
    state TEXT NOT NULL
) STRICT;
INSERT INTO "receipts" VALUES('rcp_dev01_019e704488000000000000000000006a','prm_dev01_019e7044880000000000000000000068','att_dev01_01a21fb598a148da4f8d78e17cd7ae57','tnt_dev01_019e7044880000000000000000000000','run_dev01_01a21fb59899d74a729ff303b981f35c','{"attempt_id":"att_dev01_01a21fb598a148da4f8d78e17cd7ae57","consumer":"BROKER","expires_at_ms":1800000030000,"owner_epoch":1,"pep_instance_id":"pep_dev01_019e7044880000000000000000000061","permit_id":"prm_dev01_019e7044880000000000000000000068","receipt_id":"rcp_dev01_019e704488000000000000000000006a","receipt_version":1,"reservation_id":"rsv_dev01_019e7044880000000000000000000066","revocation_gen":0,"tenant_id":"tnt_dev01_019e7044880000000000000000000000"}','USED');
CREATE TABLE reservations (
    reservation_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    period_id TEXT NOT NULL,
    experiment_id TEXT,
    action_kind TEXT,
    state TEXT NOT NULL,
    action_hash TEXT,
    cost_cap_micro INTEGER CHECK (cost_cap_micro >= 0),
    required_micro INTEGER CHECK (required_micro >= 0),
    reserved_micro INTEGER NOT NULL CHECK (reserved_micro >= 0),
    hold_micro INTEGER NOT NULL CHECK (hold_micro >= 0),
    spent_micro INTEGER NOT NULL CHECK (spent_micro >= 0),
    adjust_micro INTEGER NOT NULL CHECK (adjust_micro >= 0),
    spent_lower_micro INTEGER NOT NULL CHECK (spent_lower_micro >= 0),
    spent_known INTEGER NOT NULL CHECK (spent_known IN (0, 1)),
    allocated_at_ms INTEGER NOT NULL CHECK (allocated_at_ms >= 0),
    expires_at_ms INTEGER NOT NULL CHECK (expires_at_ms >= 0),
    permit_id TEXT,
    receipt_id TEXT
) STRICT;
INSERT INTO "reservations" VALUES('rsv_dev01_019e7044880000000000000000000066','att_dev01_01a21fb598a148da4f8d78e17cd7ae57','tnt_dev01_019e7044880000000000000000000000','usr_dev01_019e7044880000000000000000000001','tsk_dev01_01a21fb598970033d58ab19ebafa0a12','run_dev01_01a21fb59899d74a729ff303b981f35c','stp_dev01_01a21fb5989deb71f277e352ca617965','synthetic-2026-11',NULL,'TOOL_CALL','RECONCILED','sha256:fa857db881ff7acdd540617077cb318acf66e7c22eab794fe8ccdff4fc41324e',5000,0,5000,0,0,0,0,1,1800000000000,1800000060000,'prm_dev01_019e7044880000000000000000000068','rcp_dev01_019e704488000000000000000000006a');
CREATE TABLE revocation (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    revocation_gen INTEGER NOT NULL CHECK (revocation_gen >= 0)
) STRICT;
INSERT INTO "revocation" VALUES(1,0);
CREATE TABLE rt_attempts (
    attempt_id TEXT PRIMARY KEY,
    step_id TEXT NOT NULL,
    state TEXT NOT NULL
) STRICT;
INSERT INTO "rt_attempts" VALUES('att_dev01_01a21fb598a148da4f8d78e17cd7ae57','stp_dev01_01a21fb5989deb71f277e352ca617965','COMPLETED');
CREATE TABLE rt_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    status TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL CHECK (cancel_requested IN (0, 1)),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT;
INSERT INTO "rt_runs" VALUES('run_dev01_01a21fb59899d74a729ff303b981f35c','tsk_dev01_01a21fb598970033d58ab19ebafa0a12','tnt_dev01_019e7044880000000000000000000000','RECONCILIATION_REQUIRED',0,1800000000000);
CREATE TABLE rt_steps (
    step_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL
) STRICT;
INSERT INTO "rt_steps" VALUES('stp_dev01_01a21fb5989deb71f277e352ca617965','run_dev01_01a21fb59899d74a729ff303b981f35c','TOOL_CALL');
CREATE TABLE rt_tasks (
    task_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    budget_micro INTEGER NOT NULL CHECK (budget_micro >= 0),
    caps TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT;
INSERT INTO "rt_tasks" VALUES('tsk_dev01_01a21fb598970033d58ab19ebafa0a12','tnt_dev01_019e7044880000000000000000000000',5000000,'{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000}',1800000000000);
CREATE TABLE run_grants (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    grant_hash TEXT NOT NULL,
    body TEXT NOT NULL
) STRICT;
INSERT INTO "run_grants" VALUES('run_dev01_01a21fb59899d74a729ff303b981f35c','tnt_dev01_019e7044880000000000000000000000','sha256:3085d5c7b600870ae47e3d75dd36e7aa726ea1aa3c049ba144bc1e90a74ba0dc','{"access_epoch_at_grant":0,"experiment_id":null,"expires_at_ms":1800003600000,"files":{"read":["workspace/**"],"write":["workspace/out/**"]},"issued_at_ms":1799999999000,"max_cost_per_action":{"currency":"XTU","micro_units":5000},"model_profiles":[{"model_id":"scripted-model-small","profile_version":"0.1.0"}],"period_id":"synthetic-2026-11","policy_version":"policy-1","run_grant_id":"grt_dev01_019e7044880000000000000000000065","run_id":"run_dev01_01a21fb59899d74a729ff303b981f35c","synthetic_task_ref":"sha256:18827c6386e82a4172ab53e2c6f913e3b56922f1773400afa220282b0db30e62","task_id":"tsk_dev01_01a21fb598970033d58ab19ebafa0a12","tenant_id":"tnt_dev01_019e7044880000000000000000000000","tools":[{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:0482401686134e9d52335e602be40b62a225b6c59e8f602fbcedea9456ef337f","tool_id":"costly_read","version":"1.0.0"},{"artifact_hash":"sha256:bca87f5d1d6d150cea3b0b581ac63b25033c9c7d85098bb389bd40b9fc8303c3","manifest_hash":"sha256:db941c7739d1e3a22e9e552e804d30ff2965905bf9de10c58c5efd0394250906","tool_id":"read_file","version":"1.0.0"},{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:60dda8fbcad88900228c8c3e0a15b1de11eafe88baded8708d43b0f0b0ef0933","tool_id":"synthetic_change","version":"1.0.0"},{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:aa2f09d596c1d1b35271252900340d3a2fb9916cc84ba6530b5d9c9b46861823","tool_id":"synthetic_costly","version":"1.0.0"},{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:09cc39268f898a09466880756b2f7afd81fe09a49c7114edd5210974200db5b1","tool_id":"synthetic_op","version":"1.0.0"},{"artifact_hash":"sha256:bca87f5d1d6d150cea3b0b581ac63b25033c9c7d85098bb389bd40b9fc8303c3","manifest_hash":"sha256:9f171e9b16b75b7f2b2d920528d35dd8bd9ca4f247d6518b864a5cc35c3b32a6","tool_id":"write_file","version":"1.0.0"}],"user_id":"usr_dev01_019e7044880000000000000000000001"}');
CREATE TABLE run_head (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL
        REFERENCES tenant_head (tenant_id) DEFERRABLE INITIALLY DEFERRED,
    run_seq INTEGER NOT NULL CHECK (run_seq >= 1)
) STRICT;
INSERT INTO "run_head" VALUES('run_dev01_01a21fb59899d74a729ff303b981f35c','tnt_dev01_019e7044880000000000000000000000',20);
CREATE TABLE run_status_times (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    since_ms INTEGER NOT NULL CHECK (since_ms >= 0)
) STRICT;
INSERT INTO "run_status_times" VALUES('run_dev01_01a21fb59899d74a729ff303b981f35c','RECONCILIATION_REQUIRED',1800000000000);
CREATE TABLE run_windows (
    run_id TEXT PRIMARY KEY,
    max_elapsed_ms INTEGER NOT NULL CHECK (max_elapsed_ms >= 1)
) STRICT;
INSERT INTO "run_windows" VALUES('run_dev01_01a21fb59899d74a729ff303b981f35c',3600000);
CREATE TABLE runtime_owner (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    epoch INTEGER NOT NULL CHECK (epoch >= 0),
    instance_id TEXT NOT NULL,
    acquired_at_ms INTEGER NOT NULL CHECK (acquired_at_ms >= 0)
) STRICT;
INSERT INTO "runtime_owner" VALUES(1,2,'34316b82b1334863eb81c50e225dbd06',1800000000000);
CREATE TABLE safety_epochs (
    tenant_id TEXT PRIMARY KEY,
    access_epoch INTEGER NOT NULL CHECK (access_epoch >= 0)
) STRICT;
CREATE TABLE step_cap_defaults (
    run_id TEXT PRIMARY KEY,
    cap_micro INTEGER NOT NULL CHECK (cap_micro >= 0)
) STRICT;
CREATE TABLE stop_state (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    stop_seq INTEGER NOT NULL CHECK (stop_seq >= 0),
    status TEXT NOT NULL,
    PRIMARY KEY (scope_type, scope_id)
) STRICT;
INSERT INTO "stop_state" VALUES('GLOBAL','global',0,'RUNNING');
CREATE TABLE tenant_head (
    tenant_id TEXT PRIMARY KEY,
    tenant_seq INTEGER NOT NULL CHECK (tenant_seq >= 1),
    head_hash TEXT NOT NULL
) STRICT;
INSERT INTO "tenant_head" VALUES('tnt_dev01_019e7044880000000000000000000000',21,'sha256:b6193143e3ee6f9512706a04ab0bcc3ce53a017b4de7fb40e70d300c2e73d3c8');
CREATE INDEX reservations_by_step ON reservations (step_id);
CREATE INDEX reservations_by_run ON reservations (run_id);
CREATE INDEX reservations_by_task ON reservations (task_id);
COMMIT;
