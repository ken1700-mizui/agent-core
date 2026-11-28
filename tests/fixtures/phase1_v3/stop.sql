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
INSERT INTO "audit_log" VALUES(2,1800000000000,'GRANT_INSTALLED','{"run":"run_dev01_01a21fb5991c9f69287e237aee1dcccd"}','ALLOW',NULL,'policy-1',0,'sha256:68b55d70e46e436f4f022dd65651f128910360b6ddfcf5baf8df696d3e31376d','sha256:3f5cfe06309d1757fb6a4fd25598a1b3220b5e550d1d3cff6a62d4203bbd6761');
INSERT INTO "audit_log" VALUES(3,1800000000000,'POLICY_ALLOWED','{"attempt":"att_dev01_01a21fb59925c68a45b6bb2dfc10686e"}','ALLOW',NULL,'policy-1',0,'sha256:3f5cfe06309d1757fb6a4fd25598a1b3220b5e550d1d3cff6a62d4203bbd6761','sha256:08e04523b0f05afb605d5e87dd0a5cdca1be3466e9edbba92f4e5d6c55691ccf');
INSERT INTO "audit_log" VALUES(4,1800000000000,'REVOCATION_GEN','{"gen":"1"}','REVOKE',NULL,NULL,NULL,'sha256:08e04523b0f05afb605d5e87dd0a5cdca1be3466e9edbba92f4e5d6c55691ccf','sha256:16ea91346a96f15d997a52ff58aa27a16096c94c4463bef9fc328fcc82ffca52');
INSERT INTO "audit_log" VALUES(5,1800000000000,'STOP','{"scope":"RUN:run_dev01_01a21fb5991c9f69287e237aee1dcccd"}','STOP',NULL,NULL,NULL,'sha256:16ea91346a96f15d997a52ff58aa27a16096c94c4463bef9fc328fcc82ffca52','sha256:928c90177cd6af5bc519e11d8360f498ae93141da59a1cb847997bbe1ed12049');
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
INSERT INTO "budget_caps" VALUES('STEP','stp_dev01_01a21fb5992150faf06f01f2c41ee251',10000);
INSERT INTO "budget_caps" VALUES('RUN','run_dev01_01a21fb5991c9f69287e237aee1dcccd',10000);
INSERT INTO "budget_caps" VALUES('TASK','tsk_dev01_01a21fb5991be2b839858811c2c82c34',10000);
INSERT INTO "budget_caps" VALUES('USER','usr_dev01_019e7044880000000000000000000001',10000);
INSERT INTO "budget_caps" VALUES('TENANT','tnt_dev01_019e7044880000000000000000000000',10000);
INSERT INTO "budget_caps" VALUES('PERIOD','synthetic-2026-11',10000);
CREATE TABLE counter_caps (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    counter TEXT NOT NULL,
    cap INTEGER NOT NULL CHECK (cap >= 0),
    PRIMARY KEY (scope_type, scope_id, counter)
) STRICT;
INSERT INTO "counter_caps" VALUES('RUN','run_dev01_01a21fb5991c9f69287e237aee1dcccd','MODEL_CALLS',100);
INSERT INTO "counter_caps" VALUES('TASK','tsk_dev01_01a21fb5991be2b839858811c2c82c34','MODEL_CALLS',100);
INSERT INTO "counter_caps" VALUES('RUN','run_dev01_01a21fb5991c9f69287e237aee1dcccd','TOOL_CALLS',100);
INSERT INTO "counter_caps" VALUES('TASK','tsk_dev01_01a21fb5991be2b839858811c2c82c34','TOOL_CALLS',100);
INSERT INTO "counter_caps" VALUES('RUN','run_dev01_01a21fb5991c9f69287e237aee1dcccd','RETRIES',100);
INSERT INTO "counter_caps" VALUES('TASK','tsk_dev01_01a21fb5991be2b839858811c2c82c34','RETRIES',100);
INSERT INTO "counter_caps" VALUES('RUN','run_dev01_01a21fb5991c9f69287e237aee1dcccd','WORK_UNITS',100);
INSERT INTO "counter_caps" VALUES('TASK','tsk_dev01_01a21fb5991be2b839858811c2c82c34','WORK_UNITS',100);
CREATE TABLE counter_usage (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    counter TEXT NOT NULL,
    used INTEGER NOT NULL CHECK (used >= 0),
    PRIMARY KEY (scope_type, scope_id, counter)
) STRICT;
INSERT INTO "counter_usage" VALUES('RUN','run_dev01_01a21fb5991c9f69287e237aee1dcccd','TOOL_CALLS',1);
INSERT INTO "counter_usage" VALUES('TASK','tsk_dev01_01a21fb5991be2b839858811c2c82c34','TOOL_CALLS',1);
INSERT INTO "counter_usage" VALUES('RUN','run_dev01_01a21fb5991c9f69287e237aee1dcccd','WORK_UNITS',1);
INSERT INTO "counter_usage" VALUES('TASK','tsk_dev01_01a21fb5991be2b839858811c2c82c34','WORK_UNITS',1);
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
INSERT INTO "decisions" VALUES('att_dev01_01a21fb59925c68a45b6bb2dfc10686e','tnt_dev01_019e7044880000000000000000000000','run_dev01_01a21fb5991c9f69287e237aee1dcccd','sha256:dd8cbc4b5118b11bf7c3ed5301dc34324f564d06e8eded765b53905a5544af9d','rsv_dev01_019e704488000000000000000000007e','{"access_epoch":0,"apply_change":null,"args_hash":"sha256:0c7cfd7dedb9dd04fd7e05c19a28f4b346e05ea79ad9e440a2e524d5b7082bc0","attempt_id":"att_dev01_01a21fb59925c68a45b6bb2dfc10686e","cost_basis":{"estimate":{"currency":"XTU","micro_units":0},"kind":"tool"},"cost_cap":{"currency":"XTU","micro_units":5000},"descriptor_version":3,"destinations":[],"effect_class":"READ_ONLY","expires_at_ms":1800000060000,"hash_scheme":"SYNTHETIC_SHA256","kind":"TOOL_CALL","model":null,"policy_version":"policy-1","request_hash":null,"reservation_id":"rsv_dev01_019e704488000000000000000000007e","run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","sandbox_profile_hash":null,"step_id":"stp_dev01_01a21fb5992150faf06f01f2c41ee251","target_resources":[],"task_id":"tsk_dev01_01a21fb5991be2b839858811c2c82c34","tenant_id":"tnt_dev01_019e7044880000000000000000000000","tool":{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:09cc39268f898a09466880756b2f7afd81fe09a49c7114edd5210974200db5b1","tool_id":"synthetic_op","version":"1.0.0"},"workspace_ref":"sha256:20c988542b445212f959e5f1094801ce10e7cae1e7832c218e8941e3bbb84b18"}','{"approval_ref":"sha256:e62ef87580cf4336a581d63305583a27edc96c4a48f2f5bffec9d238981714ce","artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest":{"approval_conditions":[],"cost_estimate":{"currency":"XTU","micro_units":0},"destinations":[],"effect_class":"READ_ONLY","external_guarantee":"NONE","input_schema":{"fields":[{"apply":null,"max_length":256,"maximum":null,"minimum":null,"name":"value","required":true,"resource":null,"type":"STRING"}],"max_bytes":1024},"kind":"BUILTIN","manifest_hash":"sha256:09cc39268f898a09466880756b2f7afd81fe09a49c7114edd5210974200db5b1","max_output_bytes":70000,"output_schema":{"fields":[{"apply":null,"max_length":65536,"maximum":null,"minimum":null,"name":"content","required":true,"resource":null,"type":"STRING"},{"apply":null,"max_length":null,"maximum":null,"minimum":null,"name":"truncated","required":true,"resource":null,"type":"BOOLEAN"}],"max_bytes":70000},"required_permissions":[],"resources":[],"sandbox_profile":null,"timeout_ms":5000,"tool_id":"synthetic_op","version":"1.0.0"}}','sha256:acccacc7331e29d870e4ddf27fe54bb6b5bab5b542b7cfe886229c17cf073129',0,NULL);
CREATE TABLE deny_list (
    tenant_id TEXT NOT NULL,
    path TEXT NOT NULL,
    PRIMARY KEY (tenant_id, path)
) STRICT;
CREATE TABLE entity_times (
    entity_id TEXT PRIMARY KEY,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT;
INSERT INTO "entity_times" VALUES('tsk_dev01_01a21fb5991be2b839858811c2c82c34',1800000000000);
INSERT INTO "entity_times" VALUES('run_dev01_01a21fb5991c9f69287e237aee1dcccd',1800000000000);
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
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',1,'evt_dev01_01a21fb5991b4193fb6cd3f0f7cc28d1',NULL,NULL,1,'sha256:39b8217146847def76eb0b4f303430d5becd9f15b6312e57cf929204611708e8','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5991b4193fb6cd3f0f7cc28d1","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"CREATE","after":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":[],"status":"CREATED","task_id":"tsk_dev01_01a21fb5991be2b839858811c2c82c34"},"before":null,"entity":"task"}]},"policy_version":"policy-1","prev_hash":null,"recorded_at_ms":1795828324635,"refs":[],"revocation_gen":0,"run_id":null,"run_seq":null,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":1,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',2,'evt_dev01_01a21fb5991d655365f89d4ca7272c78','run_dev01_01a21fb5991c9f69287e237aee1dcccd',1,1,'sha256:357ebe4771fe1b9e3cd95d74603a14a3e8d07c176fdd53311aadbdcb9f7b435d','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5991d655365f89d4ca7272c78","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":[],"status":"ACTIVE","task_id":"tsk_dev01_01a21fb5991be2b839858811c2c82c34"},"before":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":[],"status":"CREATED","task_id":"tsk_dev01_01a21fb5991be2b839858811c2c82c34"},"entity":"task"},{"action":"CREATE","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000007b","run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","status":"CREATED","task_id":"tsk_dev01_01a21fb5991be2b839858811c2c82c34"},"before":null,"entity":"run","evidence":null},{"action":"ATTACH_RUN","after":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":["run_dev01_01a21fb5991c9f69287e237aee1dcccd"],"status":"ACTIVE","task_id":"tsk_dev01_01a21fb5991be2b839858811c2c82c34"},"before":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":[],"status":"ACTIVE","task_id":"tsk_dev01_01a21fb5991be2b839858811c2c82c34"},"entity":"task"}]},"policy_version":"policy-1","prev_hash":"sha256:39b8217146847def76eb0b4f303430d5becd9f15b6312e57cf929204611708e8","recorded_at_ms":1795828324637,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":1,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":2,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',3,'evt_dev01_01a21fb5991e9e88b254dc610cddd92a','run_dev01_01a21fb5991c9f69287e237aee1dcccd',2,1,'sha256:0e422b91234ac28428f9e2762e5d1b454bf0bc73aef7321df1a623d01f8ebb91','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5991e9e88b254dc610cddd92a","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000007b","run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","status":"QUEUED","task_id":"tsk_dev01_01a21fb5991be2b839858811c2c82c34"},"before":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000007b","run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","status":"CREATED","task_id":"tsk_dev01_01a21fb5991be2b839858811c2c82c34"},"entity":"run","evidence":null}]},"policy_version":"policy-1","prev_hash":"sha256:357ebe4771fe1b9e3cd95d74603a14a3e8d07c176fdd53311aadbdcb9f7b435d","recorded_at_ms":1795828324638,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":2,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":3,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',4,'evt_dev01_01a21fb599203b92fd254633cc33db13','run_dev01_01a21fb5991c9f69287e237aee1dcccd',3,1,'sha256:ad087438355788b3374a7319e773cefe1ea32f5eeac6de841ae8ceb52a5d7c25','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb599203b92fd254633cc33db13","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000007b","run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","status":"RUNNING","task_id":"tsk_dev01_01a21fb5991be2b839858811c2c82c34"},"before":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000007b","run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","status":"QUEUED","task_id":"tsk_dev01_01a21fb5991be2b839858811c2c82c34"},"entity":"run","evidence":null}]},"policy_version":"policy-1","prev_hash":"sha256:0e422b91234ac28428f9e2762e5d1b454bf0bc73aef7321df1a623d01f8ebb91","recorded_at_ms":1795828324640,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":3,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":4,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',5,'evt_dev01_01a21fb599215a30591e1a8d5e6e1e02','run_dev01_01a21fb5991c9f69287e237aee1dcccd',4,1,'sha256:af1139f21e95a870e2571cc87bb64d0db8a33688e12dda7df1668e0b5d91de9d','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb599215a30591e1a8d5e6e1e02","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"CREATE","after":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":4,"status":"PLANNED","step_id":"stp_dev01_01a21fb5992150faf06f01f2c41ee251"},"before":null,"entity":"step"}]},"policy_version":"policy-1","prev_hash":"sha256:ad087438355788b3374a7319e773cefe1ea32f5eeac6de841ae8ceb52a5d7c25","recorded_at_ms":1795828324641,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":4,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":5,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',6,'evt_dev01_01a21fb59922d5f345936e2bfacb0cde','run_dev01_01a21fb5991c9f69287e237aee1dcccd',5,1,'sha256:e4ef603309e368a6509f62dc9c49fa1133c94789d29bcba9b7ed698bb6d2d5ac','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb59922d5f345936e2bfacb0cde","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":4,"status":"IN_PROGRESS","step_id":"stp_dev01_01a21fb5992150faf06f01f2c41ee251"},"before":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":4,"status":"PLANNED","step_id":"stp_dev01_01a21fb5992150faf06f01f2c41ee251"},"entity":"step"}]},"policy_version":"policy-1","prev_hash":"sha256:af1139f21e95a870e2571cc87bb64d0db8a33688e12dda7df1668e0b5d91de9d","recorded_at_ms":1795828324642,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":5,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":6,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',7,'evt_dev01_01a21fb59925de3aa635f62ff52f02b3','run_dev01_01a21fb5991c9f69287e237aee1dcccd',6,1,'sha256:28ff08b9858ad404a9e322cca29781030573888b242e954c8d3fa799e3dbb9bb','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb59925de3aa635f62ff52f02b3","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"CREATE","after":{"action_hash":null,"approval_required":null,"attempt_id":"att_dev01_01a21fb59925c68a45b6bb2dfc10686e","permit_id":null,"receipt_id":null,"reservation_id":null,"state":"PROPOSED","step_id":"stp_dev01_01a21fb5992150faf06f01f2c41ee251"},"before":null,"entity":"attempt","evidence":null,"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:e4ef603309e368a6509f62dc9c49fa1133c94789d29bcba9b7ed698bb6d2d5ac","recorded_at_ms":1795828324645,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":6,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":7,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',8,'evt_dev01_01a21fb599260668c2b100d9cd2e1769','run_dev01_01a21fb5991c9f69287e237aee1dcccd',7,1,'sha256:a3c242ac56c436176927f14ee1fb94c1d19f60e298d0cae6fb92c3deda3b93fd','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb599260668c2b100d9cd2e1769","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":null,"approval_required":null,"attempt_id":"att_dev01_01a21fb59925c68a45b6bb2dfc10686e","permit_id":null,"receipt_id":null,"reservation_id":null,"state":"VALIDATED","step_id":"stp_dev01_01a21fb5992150faf06f01f2c41ee251"},"before":{"action_hash":null,"approval_required":null,"attempt_id":"att_dev01_01a21fb59925c68a45b6bb2dfc10686e","permit_id":null,"receipt_id":null,"reservation_id":null,"state":"PROPOSED","step_id":"stp_dev01_01a21fb5992150faf06f01f2c41ee251"},"entity":"attempt","evidence":{"kind":"validation","schema_ref":"sha256:df0ad6e43880f09c90ebf95f19110178aba6890df0010ebda7485029e2b543b4"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:28ff08b9858ad404a9e322cca29781030573888b242e954c8d3fa799e3dbb9bb","recorded_at_ms":1795828324646,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":7,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":8,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',9,'evt_dev01_01a21fb59928e6604b9c899af4edc082','run_dev01_01a21fb5991c9f69287e237aee1dcccd',8,1,'sha256:e574f416ce9485a542972dea0cdffbd500ed5d072baa3643bb8d87bdfd22130f','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb59928e6604b9c899af4edc082","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:dd8cbc4b5118b11bf7c3ed5301dc34324f564d06e8eded765b53905a5544af9d","approval_required":null,"attempt_id":"att_dev01_01a21fb59925c68a45b6bb2dfc10686e","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e704488000000000000000000007e","state":"CONTENT_FIXED","step_id":"stp_dev01_01a21fb5992150faf06f01f2c41ee251"},"before":{"action_hash":null,"approval_required":null,"attempt_id":"att_dev01_01a21fb59925c68a45b6bb2dfc10686e","permit_id":null,"receipt_id":null,"reservation_id":null,"state":"VALIDATED","step_id":"stp_dev01_01a21fb5992150faf06f01f2c41ee251"},"entity":"attempt","evidence":{"action_hash":"sha256:dd8cbc4b5118b11bf7c3ed5301dc34324f564d06e8eded765b53905a5544af9d","kind":"content_fixed","reservation_id":"rsv_dev01_019e704488000000000000000000007e"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:a3c242ac56c436176927f14ee1fb94c1d19f60e298d0cae6fb92c3deda3b93fd","recorded_at_ms":1795828324648,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":8,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":9,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',10,'evt_dev01_01a21fb5992abb281e9064baccbfc974','run_dev01_01a21fb5991c9f69287e237aee1dcccd',9,1,'sha256:ef90c7c089b232be8bc8873f80d1ee4ddce9def07ebf70434024c95411cff348','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5992abb281e9064baccbfc974","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:dd8cbc4b5118b11bf7c3ed5301dc34324f564d06e8eded765b53905a5544af9d","approval_required":false,"attempt_id":"att_dev01_01a21fb59925c68a45b6bb2dfc10686e","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e704488000000000000000000007e","state":"POLICY_ALLOWED","step_id":"stp_dev01_01a21fb5992150faf06f01f2c41ee251"},"before":{"action_hash":"sha256:dd8cbc4b5118b11bf7c3ed5301dc34324f564d06e8eded765b53905a5544af9d","approval_required":null,"attempt_id":"att_dev01_01a21fb59925c68a45b6bb2dfc10686e","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e704488000000000000000000007e","state":"CONTENT_FIXED","step_id":"stp_dev01_01a21fb5992150faf06f01f2c41ee251"},"entity":"attempt","evidence":{"action_hash":"sha256:dd8cbc4b5118b11bf7c3ed5301dc34324f564d06e8eded765b53905a5544af9d","approval_required":false,"decision_ref":"sha256:86ae35d58a6aa3b5742df94ef9d7162219f0106a911ae1954c1f0604aaec805d","kind":"policy_decision","policy_version":"policy-1"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:e574f416ce9485a542972dea0cdffbd500ed5d072baa3643bb8d87bdfd22130f","recorded_at_ms":1795828324650,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":9,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":10,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',11,'evt_dev01_01a21fb5992c8f626f25979af170996a','run_dev01_01a21fb5991c9f69287e237aee1dcccd',10,1,'sha256:e6d802fe063b94ed2850a9dd960616353080c6c0801f38e3e223e6b3c20d6696','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5992c8f626f25979af170996a","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:dd8cbc4b5118b11bf7c3ed5301dc34324f564d06e8eded765b53905a5544af9d","approval_required":false,"attempt_id":"att_dev01_01a21fb59925c68a45b6bb2dfc10686e","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e704488000000000000000000007e","state":"BUDGET_RESERVED","step_id":"stp_dev01_01a21fb5992150faf06f01f2c41ee251"},"before":{"action_hash":"sha256:dd8cbc4b5118b11bf7c3ed5301dc34324f564d06e8eded765b53905a5544af9d","approval_required":false,"attempt_id":"att_dev01_01a21fb59925c68a45b6bb2dfc10686e","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e704488000000000000000000007e","state":"POLICY_ALLOWED","step_id":"stp_dev01_01a21fb5992150faf06f01f2c41ee251"},"entity":"attempt","evidence":{"action_hash":"sha256:dd8cbc4b5118b11bf7c3ed5301dc34324f564d06e8eded765b53905a5544af9d","kind":"reservation","reservation_id":"rsv_dev01_019e704488000000000000000000007e"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:ef90c7c089b232be8bc8873f80d1ee4ddce9def07ebf70434024c95411cff348","recorded_at_ms":1795828324652,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":10,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":11,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',12,'evt_dev01_01a21fb5993b28d173de6d20400b9260','run_dev01_01a21fb5991c9f69287e237aee1dcccd',11,1,'sha256:741a8350d82c6b06eb2f18723d8ce1daa18bf405b69cbbb08de48fdeb0a37291','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5993b28d173de6d20400b9260","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"cancel_requested":true,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000007b","run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","status":"CANCELLING","task_id":"tsk_dev01_01a21fb5991be2b839858811c2c82c34"},"before":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000007b","run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","status":"RUNNING","task_id":"tsk_dev01_01a21fb5991be2b839858811c2c82c34"},"entity":"run","evidence":null}]},"policy_version":"policy-1","prev_hash":"sha256:e6d802fe063b94ed2850a9dd960616353080c6c0801f38e3e223e6b3c20d6696","recorded_at_ms":1795828324667,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":11,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":12,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',13,'evt_dev01_01a21fb5993dddbcfe984ce04b965e99','run_dev01_01a21fb5991c9f69287e237aee1dcccd',12,1,'sha256:c088997640e916a3a3aba348d02710f8f90f63a52114102e70074338f24d175e','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5993dddbcfe984ce04b965e99","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:dd8cbc4b5118b11bf7c3ed5301dc34324f564d06e8eded765b53905a5544af9d","approval_required":false,"attempt_id":"att_dev01_01a21fb59925c68a45b6bb2dfc10686e","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e704488000000000000000000007e","state":"ABORTED_BEFORE_DISPATCH","step_id":"stp_dev01_01a21fb5992150faf06f01f2c41ee251"},"before":{"action_hash":"sha256:dd8cbc4b5118b11bf7c3ed5301dc34324f564d06e8eded765b53905a5544af9d","approval_required":false,"attempt_id":"att_dev01_01a21fb59925c68a45b6bb2dfc10686e","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e704488000000000000000000007e","state":"BUDGET_RESERVED","step_id":"stp_dev01_01a21fb5992150faf06f01f2c41ee251"},"entity":"attempt","evidence":{"kind":"abort","not_dispatched_ref":null,"reason":"REVOKED"},"late":true}]},"policy_version":"policy-1","prev_hash":"sha256:741a8350d82c6b06eb2f18723d8ce1daa18bf405b69cbbb08de48fdeb0a37291","recorded_at_ms":1795828324669,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":12,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":13,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',14,'evt_dev01_01a21fb5993e6f2801d318e08c8dc725','run_dev01_01a21fb5991c9f69287e237aee1dcccd',13,1,'sha256:e7f3803aebf8393ad60af99e7ca22b654743d8d12b362dcf13e38c4ad90c3ada','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5993e6f2801d318e08c8dc725","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":4,"status":"CANCELLED","step_id":"stp_dev01_01a21fb5992150faf06f01f2c41ee251"},"before":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":4,"status":"IN_PROGRESS","step_id":"stp_dev01_01a21fb5992150faf06f01f2c41ee251"},"entity":"step"}]},"policy_version":"policy-1","prev_hash":"sha256:c088997640e916a3a3aba348d02710f8f90f63a52114102e70074338f24d175e","recorded_at_ms":1795828324670,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":13,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":14,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',15,'evt_dev01_01a21fb599409ac872b239a5bbec15ad','run_dev01_01a21fb5991c9f69287e237aee1dcccd',14,1,'sha256:aaa0f7b4c97335652a2f80c378eb740ff5b669e09ccc1470d8707c034423df48','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb599409ac872b239a5bbec15ad","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"cancel_requested":true,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000007b","run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","status":"CANCELLED","task_id":"tsk_dev01_01a21fb5991be2b839858811c2c82c34"},"before":{"cancel_requested":true,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000007b","run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","status":"CANCELLING","task_id":"tsk_dev01_01a21fb5991be2b839858811c2c82c34"},"entity":"run","evidence":null}]},"policy_version":"policy-1","prev_hash":"sha256:e7f3803aebf8393ad60af99e7ca22b654743d8d12b362dcf13e38c4ad90c3ada","recorded_at_ms":1795828324672,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","run_seq":14,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":15,"type":"runtime.transition"}');
CREATE TABLE intents (
    attempt_id TEXT PRIMARY KEY,
    action_hash TEXT NOT NULL,
    reservation_id TEXT NOT NULL,
    intent_ref TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms >= 0)
) STRICT;
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
INSERT INTO "ledger_entries" VALUES(1,'rsv_dev01_019e704488000000000000000000007e','STATE','ID_ALLOCATED',0,0,0,NULL,NULL,NULL,1800000000000);
INSERT INTO "ledger_entries" VALUES(2,'rsv_dev01_019e704488000000000000000000007e','STATE','RESERVED',1000,0,0,NULL,NULL,NULL,1800000000000);
INSERT INTO "ledger_entries" VALUES(3,'rsv_dev01_019e704488000000000000000000007e','STATE','RELEASED',0,0,0,NULL,NULL,NULL,1800000000000);
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
INSERT INTO "owner_sequence" VALUES(1,4);
CREATE TABLE permits (
    permit_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    body TEXT NOT NULL,
    mac TEXT NOT NULL,
    state TEXT NOT NULL
) STRICT;
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
INSERT INTO "reservations" VALUES('rsv_dev01_019e704488000000000000000000007e','att_dev01_01a21fb59925c68a45b6bb2dfc10686e','tnt_dev01_019e7044880000000000000000000000','usr_dev01_019e7044880000000000000000000001','tsk_dev01_01a21fb5991be2b839858811c2c82c34','run_dev01_01a21fb5991c9f69287e237aee1dcccd','stp_dev01_01a21fb5992150faf06f01f2c41ee251','synthetic-2026-11',NULL,'TOOL_CALL','RELEASED','sha256:dd8cbc4b5118b11bf7c3ed5301dc34324f564d06e8eded765b53905a5544af9d',5000,0,1000,0,0,0,0,1,1800000000000,1800000060000,NULL,NULL);
CREATE TABLE revocation (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    revocation_gen INTEGER NOT NULL CHECK (revocation_gen >= 0)
) STRICT;
INSERT INTO "revocation" VALUES(1,1);
CREATE TABLE rt_attempts (
    attempt_id TEXT PRIMARY KEY,
    step_id TEXT NOT NULL,
    state TEXT NOT NULL
) STRICT;
INSERT INTO "rt_attempts" VALUES('att_dev01_01a21fb59925c68a45b6bb2dfc10686e','stp_dev01_01a21fb5992150faf06f01f2c41ee251','ABORTED_BEFORE_DISPATCH');
CREATE TABLE rt_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    status TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL CHECK (cancel_requested IN (0, 1)),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT;
INSERT INTO "rt_runs" VALUES('run_dev01_01a21fb5991c9f69287e237aee1dcccd','tsk_dev01_01a21fb5991be2b839858811c2c82c34','tnt_dev01_019e7044880000000000000000000000','CANCELLED',1,1800000000000);
CREATE TABLE rt_steps (
    step_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL
) STRICT;
INSERT INTO "rt_steps" VALUES('stp_dev01_01a21fb5992150faf06f01f2c41ee251','run_dev01_01a21fb5991c9f69287e237aee1dcccd','TOOL_CALL');
CREATE TABLE rt_tasks (
    task_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    budget_micro INTEGER NOT NULL CHECK (budget_micro >= 0),
    caps TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT;
INSERT INTO "rt_tasks" VALUES('tsk_dev01_01a21fb5991be2b839858811c2c82c34','tnt_dev01_019e7044880000000000000000000000',5000000,'{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000}',1800000000000);
CREATE TABLE run_grants (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    grant_hash TEXT NOT NULL,
    body TEXT NOT NULL
) STRICT;
INSERT INTO "run_grants" VALUES('run_dev01_01a21fb5991c9f69287e237aee1dcccd','tnt_dev01_019e7044880000000000000000000000','sha256:698b55fa136d8ada66c922f78724dafe5f4a5e5334b0a094ab2fe878cce091cd','{"access_epoch_at_grant":0,"experiment_id":null,"expires_at_ms":1800003600000,"files":{"read":["workspace/**"],"write":["workspace/out/**"]},"issued_at_ms":1799999999000,"max_cost_per_action":{"currency":"XTU","micro_units":5000},"model_profiles":[{"model_id":"scripted-model-small","profile_version":"0.1.0"}],"period_id":"synthetic-2026-11","policy_version":"policy-1","run_grant_id":"grt_dev01_019e704488000000000000000000007d","run_id":"run_dev01_01a21fb5991c9f69287e237aee1dcccd","synthetic_task_ref":"sha256:18827c6386e82a4172ab53e2c6f913e3b56922f1773400afa220282b0db30e62","task_id":"tsk_dev01_01a21fb5991be2b839858811c2c82c34","tenant_id":"tnt_dev01_019e7044880000000000000000000000","tools":[{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:0482401686134e9d52335e602be40b62a225b6c59e8f602fbcedea9456ef337f","tool_id":"costly_read","version":"1.0.0"},{"artifact_hash":"sha256:bca87f5d1d6d150cea3b0b581ac63b25033c9c7d85098bb389bd40b9fc8303c3","manifest_hash":"sha256:db941c7739d1e3a22e9e552e804d30ff2965905bf9de10c58c5efd0394250906","tool_id":"read_file","version":"1.0.0"},{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:60dda8fbcad88900228c8c3e0a15b1de11eafe88baded8708d43b0f0b0ef0933","tool_id":"synthetic_change","version":"1.0.0"},{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:aa2f09d596c1d1b35271252900340d3a2fb9916cc84ba6530b5d9c9b46861823","tool_id":"synthetic_costly","version":"1.0.0"},{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:09cc39268f898a09466880756b2f7afd81fe09a49c7114edd5210974200db5b1","tool_id":"synthetic_op","version":"1.0.0"},{"artifact_hash":"sha256:bca87f5d1d6d150cea3b0b581ac63b25033c9c7d85098bb389bd40b9fc8303c3","manifest_hash":"sha256:9f171e9b16b75b7f2b2d920528d35dd8bd9ca4f247d6518b864a5cc35c3b32a6","tool_id":"write_file","version":"1.0.0"}],"user_id":"usr_dev01_019e7044880000000000000000000001"}');
CREATE TABLE run_head (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL
        REFERENCES tenant_head (tenant_id) DEFERRABLE INITIALLY DEFERRED,
    run_seq INTEGER NOT NULL CHECK (run_seq >= 1)
) STRICT;
INSERT INTO "run_head" VALUES('run_dev01_01a21fb5991c9f69287e237aee1dcccd','tnt_dev01_019e7044880000000000000000000000',14);
CREATE TABLE run_status_times (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    since_ms INTEGER NOT NULL CHECK (since_ms >= 0)
) STRICT;
INSERT INTO "run_status_times" VALUES('run_dev01_01a21fb5991c9f69287e237aee1dcccd','CANCELLED',1800000000000);
CREATE TABLE run_windows (
    run_id TEXT PRIMARY KEY,
    max_elapsed_ms INTEGER NOT NULL CHECK (max_elapsed_ms >= 1)
) STRICT;
INSERT INTO "run_windows" VALUES('run_dev01_01a21fb5991c9f69287e237aee1dcccd',3600000);
CREATE TABLE runtime_owner (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    epoch INTEGER NOT NULL CHECK (epoch >= 0),
    instance_id TEXT NOT NULL,
    acquired_at_ms INTEGER NOT NULL CHECK (acquired_at_ms >= 0)
) STRICT;
INSERT INTO "runtime_owner" VALUES(1,2,'7a3266740819ec517daa44746b925d99',1800000000000);
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
INSERT INTO "stop_state" VALUES('RUN','run_dev01_01a21fb5991c9f69287e237aee1dcccd',1,'STOPPED');
CREATE TABLE tenant_head (
    tenant_id TEXT PRIMARY KEY,
    tenant_seq INTEGER NOT NULL CHECK (tenant_seq >= 1),
    head_hash TEXT NOT NULL
) STRICT;
INSERT INTO "tenant_head" VALUES('tnt_dev01_019e7044880000000000000000000000',15,'sha256:aaa0f7b4c97335652a2f80c378eb740ff5b669e09ccc1470d8707c034423df48');
CREATE INDEX reservations_by_step ON reservations (step_id);
CREATE INDEX reservations_by_run ON reservations (run_id);
CREATE INDEX reservations_by_task ON reservations (task_id);
COMMIT;
