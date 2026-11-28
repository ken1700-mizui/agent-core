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
INSERT INTO "audit_log" VALUES(2,1800000000000,'GRANT_INSTALLED','{"run":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e"}','ALLOW',NULL,'policy-1',0,'sha256:68b55d70e46e436f4f022dd65651f128910360b6ddfcf5baf8df696d3e31376d','sha256:efc34a5feee017974229c9ca912d8d684af97c66e412877b17c7e85b3b8ee17f');
INSERT INTO "audit_log" VALUES(3,1800000000000,'POLICY_ALLOWED','{"attempt":"att_dev01_01a21fb598e8613a541cee0f7da995c4"}','ALLOW',NULL,'policy-1',0,'sha256:efc34a5feee017974229c9ca912d8d684af97c66e412877b17c7e85b3b8ee17f','sha256:fdbb221d94cdfc46cb49d367cb1949f463712477382d5cc39197c578b0c6cd2f');
INSERT INTO "audit_log" VALUES(4,1800000000000,'PERMIT_ISSUED','{"attempt":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit":"prm_dev01_019e7044880000000000000000000074"}','ALLOW',NULL,'policy-1',0,'sha256:fdbb221d94cdfc46cb49d367cb1949f463712477382d5cc39197c578b0c6cd2f','sha256:e22a36ddff73f2b1bcc2251e9f7f6b70a4dc12808021c42ada4f4b7ad5d8abe5');
INSERT INTO "audit_log" VALUES(5,1800000000000,'PERMIT_CONSUMED','{"attempt":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit":"prm_dev01_019e7044880000000000000000000074","receipt":"rcp_dev01_019e7044880000000000000000000076"}','ALLOW',NULL,'policy-1',0,'sha256:e22a36ddff73f2b1bcc2251e9f7f6b70a4dc12808021c42ada4f4b7ad5d8abe5','sha256:287ab790385992c89cb441a55eb5b473df8a30c3c156ce3f6b5c3ce3c99e6cc5');
INSERT INTO "audit_log" VALUES(6,1800000000000,'REVOCATION_GEN','{"gen":"1"}','REVOKE',NULL,NULL,NULL,'sha256:287ab790385992c89cb441a55eb5b473df8a30c3c156ce3f6b5c3ce3c99e6cc5','sha256:c94fa2f9a24085c147d61c823e60c9ff0c6b707c6366138d6b137869c73c36b2');
INSERT INTO "audit_log" VALUES(7,1800000000000,'RESTART_REVOKED','{"owner_epoch":"2"}','REVOKE',NULL,NULL,NULL,'sha256:c94fa2f9a24085c147d61c823e60c9ff0c6b707c6366138d6b137869c73c36b2','sha256:9e2c97b1a7b3c9fb1c73e04ba6b686e6b779d9bfa1de83a761386bf2609b9a02');
INSERT INTO "audit_log" VALUES(8,1800000000000,'REVOCATION_GEN','{"gen":"2"}','REVOKE',NULL,NULL,NULL,'sha256:9e2c97b1a7b3c9fb1c73e04ba6b686e6b779d9bfa1de83a761386bf2609b9a02','sha256:564df9d120f6042fea85934b6c2499423d37806ac98512320f25232b08bacc70');
INSERT INTO "audit_log" VALUES(9,1800000000000,'STOP','{"scope":"RUN:run_dev01_01a21fb598dfbf36f220caa9695e5e8e"}','STOP',NULL,NULL,NULL,'sha256:564df9d120f6042fea85934b6c2499423d37806ac98512320f25232b08bacc70','sha256:22a74b2ad09b071301c15f3142c79a17d49161b5984a9cd132490657137fc533');
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
INSERT INTO "budget_caps" VALUES('STEP','stp_dev01_01a21fb598e4be44ede53d2dd2009e50',10000);
INSERT INTO "budget_caps" VALUES('RUN','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',10000);
INSERT INTO "budget_caps" VALUES('TASK','tsk_dev01_01a21fb598deed1fabd1592755964c01',10000);
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
INSERT INTO "counter_caps" VALUES('RUN','run_dev01_01a21fb598dfbf36f220caa9695e5e8e','MODEL_CALLS',100);
INSERT INTO "counter_caps" VALUES('TASK','tsk_dev01_01a21fb598deed1fabd1592755964c01','MODEL_CALLS',100);
INSERT INTO "counter_caps" VALUES('RUN','run_dev01_01a21fb598dfbf36f220caa9695e5e8e','TOOL_CALLS',100);
INSERT INTO "counter_caps" VALUES('TASK','tsk_dev01_01a21fb598deed1fabd1592755964c01','TOOL_CALLS',100);
INSERT INTO "counter_caps" VALUES('RUN','run_dev01_01a21fb598dfbf36f220caa9695e5e8e','RETRIES',100);
INSERT INTO "counter_caps" VALUES('TASK','tsk_dev01_01a21fb598deed1fabd1592755964c01','RETRIES',100);
INSERT INTO "counter_caps" VALUES('RUN','run_dev01_01a21fb598dfbf36f220caa9695e5e8e','WORK_UNITS',100);
INSERT INTO "counter_caps" VALUES('TASK','tsk_dev01_01a21fb598deed1fabd1592755964c01','WORK_UNITS',100);
CREATE TABLE counter_usage (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    counter TEXT NOT NULL,
    used INTEGER NOT NULL CHECK (used >= 0),
    PRIMARY KEY (scope_type, scope_id, counter)
) STRICT;
INSERT INTO "counter_usage" VALUES('RUN','run_dev01_01a21fb598dfbf36f220caa9695e5e8e','TOOL_CALLS',1);
INSERT INTO "counter_usage" VALUES('TASK','tsk_dev01_01a21fb598deed1fabd1592755964c01','TOOL_CALLS',1);
INSERT INTO "counter_usage" VALUES('RUN','run_dev01_01a21fb598dfbf36f220caa9695e5e8e','WORK_UNITS',1);
INSERT INTO "counter_usage" VALUES('TASK','tsk_dev01_01a21fb598deed1fabd1592755964c01','WORK_UNITS',1);
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
INSERT INTO "decisions" VALUES('att_dev01_01a21fb598e8613a541cee0f7da995c4','tnt_dev01_019e7044880000000000000000000000','run_dev01_01a21fb598dfbf36f220caa9695e5e8e','sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236','rsv_dev01_019e7044880000000000000000000072','{"access_epoch":0,"apply_change":null,"args_hash":"sha256:0c7cfd7dedb9dd04fd7e05c19a28f4b346e05ea79ad9e440a2e524d5b7082bc0","attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","cost_basis":{"estimate":{"currency":"XTU","micro_units":0},"kind":"tool"},"cost_cap":{"currency":"XTU","micro_units":5000},"descriptor_version":3,"destinations":[],"effect_class":"READ_ONLY","expires_at_ms":1800000060000,"hash_scheme":"SYNTHETIC_SHA256","kind":"TOOL_CALL","model":null,"policy_version":"policy-1","request_hash":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000072","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","sandbox_profile_hash":null,"step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50","target_resources":[],"task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01","tenant_id":"tnt_dev01_019e7044880000000000000000000000","tool":{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:09cc39268f898a09466880756b2f7afd81fe09a49c7114edd5210974200db5b1","tool_id":"synthetic_op","version":"1.0.0"},"workspace_ref":"sha256:20c988542b445212f959e5f1094801ce10e7cae1e7832c218e8941e3bbb84b18"}','{"approval_ref":"sha256:e62ef87580cf4336a581d63305583a27edc96c4a48f2f5bffec9d238981714ce","artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest":{"approval_conditions":[],"cost_estimate":{"currency":"XTU","micro_units":0},"destinations":[],"effect_class":"READ_ONLY","external_guarantee":"NONE","input_schema":{"fields":[{"apply":null,"max_length":256,"maximum":null,"minimum":null,"name":"value","required":true,"resource":null,"type":"STRING"}],"max_bytes":1024},"kind":"BUILTIN","manifest_hash":"sha256:09cc39268f898a09466880756b2f7afd81fe09a49c7114edd5210974200db5b1","max_output_bytes":70000,"output_schema":{"fields":[{"apply":null,"max_length":65536,"maximum":null,"minimum":null,"name":"content","required":true,"resource":null,"type":"STRING"},{"apply":null,"max_length":null,"maximum":null,"minimum":null,"name":"truncated","required":true,"resource":null,"type":"BOOLEAN"}],"max_bytes":70000},"required_permissions":[],"resources":[],"sandbox_profile":null,"timeout_ms":5000,"tool_id":"synthetic_op","version":"1.0.0"}}','sha256:eef8b6ee8e893e583f6ac04f7b1a215180fe4e1d515f4e082635dde7464544be',0,NULL);
CREATE TABLE deny_list (
    tenant_id TEXT NOT NULL,
    path TEXT NOT NULL,
    PRIMARY KEY (tenant_id, path)
) STRICT;
CREATE TABLE entity_times (
    entity_id TEXT PRIMARY KEY,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT;
INSERT INTO "entity_times" VALUES('tsk_dev01_01a21fb598deed1fabd1592755964c01',1800000000000);
INSERT INTO "entity_times" VALUES('run_dev01_01a21fb598dfbf36f220caa9695e5e8e',1800000000000);
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
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',1,'evt_dev01_01a21fb598de28307d5b42e05ce69fc4',NULL,NULL,1,'sha256:8fc5aa57e7a3007a48ec7cd27e7c6251b56e070f3d72e2d3a3c8d535dff183ce','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598de28307d5b42e05ce69fc4","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"CREATE","after":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":[],"status":"CREATED","task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01"},"before":null,"entity":"task"}]},"policy_version":"policy-1","prev_hash":null,"recorded_at_ms":1795828324574,"refs":[],"revocation_gen":0,"run_id":null,"run_seq":null,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":1,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',2,'evt_dev01_01a21fb598df3ebaee635422924d0754','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',1,1,'sha256:ad4089e7e6979d68a2f7f554f6970cb7de97d23614d9f597e54de9f9c74f9f66','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598df3ebaee635422924d0754","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":[],"status":"ACTIVE","task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01"},"before":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":[],"status":"CREATED","task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01"},"entity":"task"},{"action":"CREATE","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000006f","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","status":"CREATED","task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01"},"before":null,"entity":"run","evidence":null},{"action":"ATTACH_RUN","after":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":["run_dev01_01a21fb598dfbf36f220caa9695e5e8e"],"status":"ACTIVE","task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01"},"before":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":[],"status":"ACTIVE","task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01"},"entity":"task"}]},"policy_version":"policy-1","prev_hash":"sha256:8fc5aa57e7a3007a48ec7cd27e7c6251b56e070f3d72e2d3a3c8d535dff183ce","recorded_at_ms":1795828324575,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":1,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":2,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',3,'evt_dev01_01a21fb598e1f17ae3aeaee34d36d3cc','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',2,1,'sha256:c34c36b870dbce567b64d8a14808b548bcdfc153a3cf4b7969c948b0ead5ce28','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598e1f17ae3aeaee34d36d3cc","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000006f","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","status":"QUEUED","task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01"},"before":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000006f","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","status":"CREATED","task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01"},"entity":"run","evidence":null}]},"policy_version":"policy-1","prev_hash":"sha256:ad4089e7e6979d68a2f7f554f6970cb7de97d23614d9f597e54de9f9c74f9f66","recorded_at_ms":1795828324577,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":2,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":3,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',4,'evt_dev01_01a21fb598e3280dcb93367355aa8c82','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',3,1,'sha256:e44330de2a8927dc53c89f4e5376ff5ebc5dc9e48e307a9bc4a6f03c1e09191a','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598e3280dcb93367355aa8c82","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000006f","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","status":"RUNNING","task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01"},"before":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000006f","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","status":"QUEUED","task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01"},"entity":"run","evidence":null}]},"policy_version":"policy-1","prev_hash":"sha256:c34c36b870dbce567b64d8a14808b548bcdfc153a3cf4b7969c948b0ead5ce28","recorded_at_ms":1795828324579,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":3,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":4,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',5,'evt_dev01_01a21fb598e45083d243ecaa05d37c32','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',4,1,'sha256:8034bcade313a4379b82ec0892e5b9bf9b77b584b4c6c545755f67a92db70912','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598e45083d243ecaa05d37c32","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"CREATE","after":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":4,"status":"PLANNED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"before":null,"entity":"step"}]},"policy_version":"policy-1","prev_hash":"sha256:e44330de2a8927dc53c89f4e5376ff5ebc5dc9e48e307a9bc4a6f03c1e09191a","recorded_at_ms":1795828324580,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":4,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":5,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',6,'evt_dev01_01a21fb598e53f8ff34a644fee8dc30d','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',5,1,'sha256:ab2ca7f2cd60e9b11889c899cc7989720499daf7bc9d7e570e78de840d4349de','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598e53f8ff34a644fee8dc30d","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":4,"status":"IN_PROGRESS","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"before":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":4,"status":"PLANNED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"entity":"step"}]},"policy_version":"policy-1","prev_hash":"sha256:8034bcade313a4379b82ec0892e5b9bf9b77b584b4c6c545755f67a92db70912","recorded_at_ms":1795828324581,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":5,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":6,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',7,'evt_dev01_01a21fb598e89bb3d93f1f0b86320537','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',6,1,'sha256:181088f40dd195081b96d3bd18fae06f7ab58e18360978968309861835003964','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598e89bb3d93f1f0b86320537","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"CREATE","after":{"action_hash":null,"approval_required":null,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":null,"receipt_id":null,"reservation_id":null,"state":"PROPOSED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"before":null,"entity":"attempt","evidence":null,"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:ab2ca7f2cd60e9b11889c899cc7989720499daf7bc9d7e570e78de840d4349de","recorded_at_ms":1795828324584,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":6,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":7,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',8,'evt_dev01_01a21fb598e94d7a4e76e395ffa4df5a','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',7,1,'sha256:bce8155b81926c4f2154d826be7a2ac3ae986cf7a7815699b5301cbc59923392','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598e94d7a4e76e395ffa4df5a","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":null,"approval_required":null,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":null,"receipt_id":null,"reservation_id":null,"state":"VALIDATED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"before":{"action_hash":null,"approval_required":null,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":null,"receipt_id":null,"reservation_id":null,"state":"PROPOSED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"entity":"attempt","evidence":{"kind":"validation","schema_ref":"sha256:df0ad6e43880f09c90ebf95f19110178aba6890df0010ebda7485029e2b543b4"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:181088f40dd195081b96d3bd18fae06f7ab58e18360978968309861835003964","recorded_at_ms":1795828324585,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":7,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":8,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',9,'evt_dev01_01a21fb598ea4dddc7fffaeb37e2cae8','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',8,1,'sha256:331e3df51138e364b798580cad42e77eaba977623f51e9ffa801455c3dece598','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598ea4dddc7fffaeb37e2cae8","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","approval_required":null,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000072","state":"CONTENT_FIXED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"before":{"action_hash":null,"approval_required":null,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":null,"receipt_id":null,"reservation_id":null,"state":"VALIDATED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"entity":"attempt","evidence":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","kind":"content_fixed","reservation_id":"rsv_dev01_019e7044880000000000000000000072"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:bce8155b81926c4f2154d826be7a2ac3ae986cf7a7815699b5301cbc59923392","recorded_at_ms":1795828324586,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":8,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":9,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',10,'evt_dev01_01a21fb598ecc1657575b61fe4688633','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',9,1,'sha256:52b72dce47c22815c175e08c351fb1aa8efaa5f54b07a287ef945b2e757529a8','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598ecc1657575b61fe4688633","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","approval_required":false,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000072","state":"POLICY_ALLOWED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"before":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","approval_required":null,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000072","state":"CONTENT_FIXED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"entity":"attempt","evidence":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","approval_required":false,"decision_ref":"sha256:86ae35d58a6aa3b5742df94ef9d7162219f0106a911ae1954c1f0604aaec805d","kind":"policy_decision","policy_version":"policy-1"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:331e3df51138e364b798580cad42e77eaba977623f51e9ffa801455c3dece598","recorded_at_ms":1795828324588,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":9,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":10,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',11,'evt_dev01_01a21fb598ef6dad5a9ccd8f59259c71','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',10,1,'sha256:95e52d3f20012d321fb882aafb9819ed9e5ea719b4d59af85f61f03426d1c479','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598ef6dad5a9ccd8f59259c71","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","approval_required":false,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000072","state":"BUDGET_RESERVED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"before":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","approval_required":false,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000072","state":"POLICY_ALLOWED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"entity":"attempt","evidence":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","kind":"reservation","reservation_id":"rsv_dev01_019e7044880000000000000000000072"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:52b72dce47c22815c175e08c351fb1aa8efaa5f54b07a287ef945b2e757529a8","recorded_at_ms":1795828324591,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":10,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":11,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',12,'evt_dev01_01a21fb598f17ddecb1cdc058a5ce48c','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',11,1,'sha256:355f875e1fb0e542efebd90e0d9d7f6eac32cb61e8990bc20aac4db6f8b9052b','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598f17ddecb1cdc058a5ce48c","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","approval_required":false,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000072","state":"INTENT_RECORDED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"before":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","approval_required":false,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000072","state":"BUDGET_RESERVED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"entity":"attempt","evidence":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","intent_ref":"sha256:282bcbc3f0a34a8a4ac6f00c276fcf66cf3757a3332e83d92208e5079af46922","kind":"intent"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:95e52d3f20012d321fb882aafb9819ed9e5ea719b4d59af85f61f03426d1c479","recorded_at_ms":1795828324593,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":11,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":12,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',13,'evt_dev01_01a21fb598f209a7b21cd75230796754','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',12,1,'sha256:48b2097f5a92bb41d82cd2ed0f5bcbf1a3e2a15625068f497182a92957d0a424','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598f209a7b21cd75230796754","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","approval_required":false,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":"prm_dev01_019e7044880000000000000000000074","receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000072","state":"PERMIT_ISSUED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"before":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","approval_required":false,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000072","state":"INTENT_RECORDED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"entity":"attempt","evidence":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","kind":"permit_issued","permit_id":"prm_dev01_019e7044880000000000000000000074","reservation_id":"rsv_dev01_019e7044880000000000000000000072"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:355f875e1fb0e542efebd90e0d9d7f6eac32cb61e8990bc20aac4db6f8b9052b","recorded_at_ms":1795828324594,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":12,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":13,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',14,'evt_dev01_01a21fb598f587efcfa776c0566d58ee','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',13,1,'sha256:f5698d174faac5f0df7bddee93351cb50d888f7e070ce877b89cfbb183fd4ba6','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb598f587efcfa776c0566d58ee","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","approval_required":false,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":"prm_dev01_019e7044880000000000000000000074","receipt_id":"rcp_dev01_019e7044880000000000000000000076","reservation_id":"rsv_dev01_019e7044880000000000000000000072","state":"PERMIT_CONSUMED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"before":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","approval_required":false,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":"prm_dev01_019e7044880000000000000000000074","receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000072","state":"PERMIT_ISSUED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"entity":"attempt","evidence":{"kind":"permit_consumed","permit_id":"prm_dev01_019e7044880000000000000000000074","receipt_id":"rcp_dev01_019e7044880000000000000000000076"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:48b2097f5a92bb41d82cd2ed0f5bcbf1a3e2a15625068f497182a92957d0a424","recorded_at_ms":1795828324597,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":13,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":14,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',15,'evt_dev01_01a21fb59904b34d15e574e32bd4f9c3','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',14,1,'sha256:d6fe99b4152a463656282a557afe2bd3af6ffa7f73fd37cd3c3839f7f22c5516','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb59904b34d15e574e32bd4f9c3","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000006f","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","status":"RECOVERING","task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01"},"before":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000006f","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","status":"RUNNING","task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01"},"entity":"run","evidence":null}]},"policy_version":"policy-1","prev_hash":"sha256:f5698d174faac5f0df7bddee93351cb50d888f7e070ce877b89cfbb183fd4ba6","recorded_at_ms":1795828324612,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":14,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":15,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',16,'evt_dev01_01a21fb59906c16e94314a4830b7355b','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',15,1,'sha256:334d0d5f603ded9abd134f60b5cc09910fe1b71a433af19ae647ffe20c14ba45','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb59906c16e94314a4830b7355b","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","approval_required":false,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":"prm_dev01_019e7044880000000000000000000074","receipt_id":"rcp_dev01_019e7044880000000000000000000076","reservation_id":"rsv_dev01_019e7044880000000000000000000072","state":"OUTCOME_UNKNOWN","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"before":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","approval_required":false,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":"prm_dev01_019e7044880000000000000000000074","receipt_id":"rcp_dev01_019e7044880000000000000000000076","reservation_id":"rsv_dev01_019e7044880000000000000000000072","state":"PERMIT_CONSUMED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"entity":"attempt","evidence":{"kind":"outcome","receipt_id":"rcp_dev01_019e7044880000000000000000000076","report_ref":"sha256:0346143ffcc331cbb4f773affb93dd30cd68a2bc86cb3e428d3a9897ad162c3d"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:d6fe99b4152a463656282a557afe2bd3af6ffa7f73fd37cd3c3839f7f22c5516","recorded_at_ms":1795828324614,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":15,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":16,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',17,'evt_dev01_01a21fb5990e5919016c01fdfb824ed2','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',16,1,'sha256:0200bda8d50c25274188cfa22e13854f227695653b34f9d00dc47889f1ab6140','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5990e5919016c01fdfb824ed2","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","approval_required":false,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":"prm_dev01_019e7044880000000000000000000074","receipt_id":"rcp_dev01_019e7044880000000000000000000076","reservation_id":"rsv_dev01_019e7044880000000000000000000072","state":"FAILED_DEFINITE","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"before":{"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","approval_required":false,"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","permit_id":"prm_dev01_019e7044880000000000000000000074","receipt_id":"rcp_dev01_019e7044880000000000000000000076","reservation_id":"rsv_dev01_019e7044880000000000000000000072","state":"OUTCOME_UNKNOWN","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"entity":"attempt","evidence":{"evidence_ref":"sha256:1eefbeb1df24a7a4b6f2dbce928fef971e6e142fe9d4ea166b328771a05a0cff","kind":"reconciliation","outcome":"FAILED"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:334d0d5f603ded9abd134f60b5cc09910fe1b71a433af19ae647ffe20c14ba45","recorded_at_ms":1795828324622,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":16,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":17,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',18,'evt_dev01_01a21fb59910a1afc1f508a5f3390cc6','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',17,1,'sha256:00677eeeb5faeb049c8bda1ce4dd84ec1971e5e0573340821cbe61aac6265033','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb59910a1afc1f508a5f3390cc6","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000006f","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","status":"RECONCILIATION_REQUIRED","task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01"},"before":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000006f","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","status":"RECOVERING","task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01"},"entity":"run","evidence":{"evidence_ref":"sha256:6b52861490c044ffc6d69c8b77b32115a4ed13f9a9cfd2cbe134a050c53863f1","kind":"reconciliation_trigger","reason":"SESSION_LOST"}}]},"policy_version":"policy-1","prev_hash":"sha256:0200bda8d50c25274188cfa22e13854f227695653b34f9d00dc47889f1ab6140","recorded_at_ms":1795828324624,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":17,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":18,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',19,'evt_dev01_01a21fb599114ab9ac0949f69ca38577','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',18,1,'sha256:47f487ba66909095463bad4e97d5bae63d9636967f01f5d8e051b7af574b43d6','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb599114ab9ac0949f69ca38577","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":4,"status":"FAILED","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"before":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":4,"status":"IN_PROGRESS","step_id":"stp_dev01_01a21fb598e4be44ede53d2dd2009e50"},"entity":"step"}]},"policy_version":"policy-1","prev_hash":"sha256:00677eeeb5faeb049c8bda1ce4dd84ec1971e5e0573340821cbe61aac6265033","recorded_at_ms":1795828324625,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":18,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":19,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',20,'evt_dev01_01a21fb59913e024a817873aee0b52fa','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',19,1,'sha256:5abb463ba160046316639d0227afc2969ec6249c85e63cd32b96ce9e70600310','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb59913e024a817873aee0b52fa","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000006f","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","status":"FAILED","task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01"},"before":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e704488000000000000000000006f","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","status":"RECONCILIATION_REQUIRED","task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01"},"entity":"run","evidence":{"evidence_ref":"sha256:7e5953c76963ba94ab86cde80b7bd86826af4823b8ee74cb772214fd3c11c0b2","kind":"termination","reason":"RECOVERY_FAILED"}}]},"policy_version":"policy-1","prev_hash":"sha256:47f487ba66909095463bad4e97d5bae63d9636967f01f5d8e051b7af574b43d6","recorded_at_ms":1795828324627,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","run_seq":19,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":20,"type":"runtime.transition"}');
CREATE TABLE intents (
    attempt_id TEXT PRIMARY KEY,
    action_hash TEXT NOT NULL,
    reservation_id TEXT NOT NULL,
    intent_ref TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms >= 0)
) STRICT;
INSERT INTO "intents" VALUES('att_dev01_01a21fb598e8613a541cee0f7da995c4','sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236','rsv_dev01_019e7044880000000000000000000072','sha256:282bcbc3f0a34a8a4ac6f00c276fcf66cf3757a3332e83d92208e5079af46922',1800000000000);
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
INSERT INTO "ledger_entries" VALUES(1,'rsv_dev01_019e7044880000000000000000000072','STATE','ID_ALLOCATED',0,0,0,NULL,NULL,NULL,1800000000000);
INSERT INTO "ledger_entries" VALUES(2,'rsv_dev01_019e7044880000000000000000000072','STATE','RESERVED',1000,0,0,NULL,NULL,NULL,1800000000000);
INSERT INTO "ledger_entries" VALUES(3,'rsv_dev01_019e7044880000000000000000000072','STATE','CONSUMED',1000,0,0,NULL,NULL,NULL,1800000000000);
INSERT INTO "ledger_entries" VALUES(4,'rsv_dev01_019e7044880000000000000000000072','STATE','UNKNOWN',1000,0,0,NULL,NULL,NULL,1800000000000);
INSERT INTO "ledger_entries" VALUES(5,'rsv_dev01_019e7044880000000000000000000072','STATE','RECONCILED',0,0,0,NULL,NULL,NULL,1800000000000);
INSERT INTO "ledger_entries" VALUES(6,'rsv_dev01_019e7044880000000000000000000072','RECONCILIATION',NULL,0,0,0,NULL,NULL,'sha256:1eefbeb1df24a7a4b6f2dbce928fef971e6e142fe9d4ea166b328771a05a0cff',1800000000000);
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
INSERT INTO "permits" VALUES('prm_dev01_019e7044880000000000000000000074','att_dev01_01a21fb598e8613a541cee0f7da995c4','tnt_dev01_019e7044880000000000000000000000','run_dev01_01a21fb598dfbf36f220caa9695e5e8e','{"access_epoch":0,"action_hash":"sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236","attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","consumer":"BROKER","expires_at_ms":1800000030000,"issued_at_ms":1800000000000,"owner_epoch":1,"permit_id":"prm_dev01_019e7044880000000000000000000074","permit_version":1,"policy_version":"policy-1","reservation_id":"rsv_dev01_019e7044880000000000000000000072","revocation_gen":0,"run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","tenant_id":"tnt_dev01_019e7044880000000000000000000000"}','29b714d068fb793bc93c3c5d4f37fcc01b629a15d2e21aa8d0131e31cf9b3030','CONSUMED');
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
INSERT INTO "receipts" VALUES('rcp_dev01_019e7044880000000000000000000076','prm_dev01_019e7044880000000000000000000074','att_dev01_01a21fb598e8613a541cee0f7da995c4','tnt_dev01_019e7044880000000000000000000000','run_dev01_01a21fb598dfbf36f220caa9695e5e8e','{"attempt_id":"att_dev01_01a21fb598e8613a541cee0f7da995c4","consumer":"BROKER","expires_at_ms":1800000030000,"owner_epoch":1,"pep_instance_id":"pep_dev01_019e704488000000000000000000006d","permit_id":"prm_dev01_019e7044880000000000000000000074","receipt_id":"rcp_dev01_019e7044880000000000000000000076","receipt_version":1,"reservation_id":"rsv_dev01_019e7044880000000000000000000072","revocation_gen":0,"tenant_id":"tnt_dev01_019e7044880000000000000000000000"}','REVOKED');
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
INSERT INTO "reservations" VALUES('rsv_dev01_019e7044880000000000000000000072','att_dev01_01a21fb598e8613a541cee0f7da995c4','tnt_dev01_019e7044880000000000000000000000','usr_dev01_019e7044880000000000000000000001','tsk_dev01_01a21fb598deed1fabd1592755964c01','run_dev01_01a21fb598dfbf36f220caa9695e5e8e','stp_dev01_01a21fb598e4be44ede53d2dd2009e50','synthetic-2026-11',NULL,'TOOL_CALL','RECONCILED','sha256:6c0d828d153867c87b515c86b8724c3f0e955a243e7bc096af1445cdbadd5236',5000,0,1000,0,0,0,0,1,1800000000000,1800000060000,'prm_dev01_019e7044880000000000000000000074','rcp_dev01_019e7044880000000000000000000076');
CREATE TABLE revocation (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    revocation_gen INTEGER NOT NULL CHECK (revocation_gen >= 0)
) STRICT;
INSERT INTO "revocation" VALUES(1,2);
CREATE TABLE rt_attempts (
    attempt_id TEXT PRIMARY KEY,
    step_id TEXT NOT NULL,
    state TEXT NOT NULL
) STRICT;
INSERT INTO "rt_attempts" VALUES('att_dev01_01a21fb598e8613a541cee0f7da995c4','stp_dev01_01a21fb598e4be44ede53d2dd2009e50','FAILED_DEFINITE');
CREATE TABLE rt_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    status TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL CHECK (cancel_requested IN (0, 1)),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT;
INSERT INTO "rt_runs" VALUES('run_dev01_01a21fb598dfbf36f220caa9695e5e8e','tsk_dev01_01a21fb598deed1fabd1592755964c01','tnt_dev01_019e7044880000000000000000000000','FAILED',0,1800000000000);
CREATE TABLE rt_steps (
    step_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL
) STRICT;
INSERT INTO "rt_steps" VALUES('stp_dev01_01a21fb598e4be44ede53d2dd2009e50','run_dev01_01a21fb598dfbf36f220caa9695e5e8e','TOOL_CALL');
CREATE TABLE rt_tasks (
    task_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    budget_micro INTEGER NOT NULL CHECK (budget_micro >= 0),
    caps TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT;
INSERT INTO "rt_tasks" VALUES('tsk_dev01_01a21fb598deed1fabd1592755964c01','tnt_dev01_019e7044880000000000000000000000',5000000,'{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000}',1800000000000);
CREATE TABLE run_grants (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    grant_hash TEXT NOT NULL,
    body TEXT NOT NULL
) STRICT;
INSERT INTO "run_grants" VALUES('run_dev01_01a21fb598dfbf36f220caa9695e5e8e','tnt_dev01_019e7044880000000000000000000000','sha256:b7d15df2203a6aa72ac55903f8059157b336d4c8e4ff444d746e942e86f75685','{"access_epoch_at_grant":0,"experiment_id":null,"expires_at_ms":1800003600000,"files":{"read":["workspace/**"],"write":["workspace/out/**"]},"issued_at_ms":1799999999000,"max_cost_per_action":{"currency":"XTU","micro_units":5000},"model_profiles":[{"model_id":"scripted-model-small","profile_version":"0.1.0"}],"period_id":"synthetic-2026-11","policy_version":"policy-1","run_grant_id":"grt_dev01_019e7044880000000000000000000071","run_id":"run_dev01_01a21fb598dfbf36f220caa9695e5e8e","synthetic_task_ref":"sha256:18827c6386e82a4172ab53e2c6f913e3b56922f1773400afa220282b0db30e62","task_id":"tsk_dev01_01a21fb598deed1fabd1592755964c01","tenant_id":"tnt_dev01_019e7044880000000000000000000000","tools":[{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:0482401686134e9d52335e602be40b62a225b6c59e8f602fbcedea9456ef337f","tool_id":"costly_read","version":"1.0.0"},{"artifact_hash":"sha256:bca87f5d1d6d150cea3b0b581ac63b25033c9c7d85098bb389bd40b9fc8303c3","manifest_hash":"sha256:db941c7739d1e3a22e9e552e804d30ff2965905bf9de10c58c5efd0394250906","tool_id":"read_file","version":"1.0.0"},{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:60dda8fbcad88900228c8c3e0a15b1de11eafe88baded8708d43b0f0b0ef0933","tool_id":"synthetic_change","version":"1.0.0"},{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:aa2f09d596c1d1b35271252900340d3a2fb9916cc84ba6530b5d9c9b46861823","tool_id":"synthetic_costly","version":"1.0.0"},{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:09cc39268f898a09466880756b2f7afd81fe09a49c7114edd5210974200db5b1","tool_id":"synthetic_op","version":"1.0.0"},{"artifact_hash":"sha256:bca87f5d1d6d150cea3b0b581ac63b25033c9c7d85098bb389bd40b9fc8303c3","manifest_hash":"sha256:9f171e9b16b75b7f2b2d920528d35dd8bd9ca4f247d6518b864a5cc35c3b32a6","tool_id":"write_file","version":"1.0.0"}],"user_id":"usr_dev01_019e7044880000000000000000000001"}');
CREATE TABLE run_head (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL
        REFERENCES tenant_head (tenant_id) DEFERRABLE INITIALLY DEFERRED,
    run_seq INTEGER NOT NULL CHECK (run_seq >= 1)
) STRICT;
INSERT INTO "run_head" VALUES('run_dev01_01a21fb598dfbf36f220caa9695e5e8e','tnt_dev01_019e7044880000000000000000000000',19);
CREATE TABLE run_status_times (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    since_ms INTEGER NOT NULL CHECK (since_ms >= 0)
) STRICT;
INSERT INTO "run_status_times" VALUES('run_dev01_01a21fb598dfbf36f220caa9695e5e8e','FAILED',1800000000000);
CREATE TABLE run_windows (
    run_id TEXT PRIMARY KEY,
    max_elapsed_ms INTEGER NOT NULL CHECK (max_elapsed_ms >= 1)
) STRICT;
INSERT INTO "run_windows" VALUES('run_dev01_01a21fb598dfbf36f220caa9695e5e8e',3600000);
CREATE TABLE runtime_owner (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    epoch INTEGER NOT NULL CHECK (epoch >= 0),
    instance_id TEXT NOT NULL,
    acquired_at_ms INTEGER NOT NULL CHECK (acquired_at_ms >= 0)
) STRICT;
INSERT INTO "runtime_owner" VALUES(1,2,'0b1f53ecf552bbd1f1716a8045a1aa62',1800000000000);
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
INSERT INTO "stop_state" VALUES('RUN','run_dev01_01a21fb598dfbf36f220caa9695e5e8e',1,'STOPPED');
CREATE TABLE tenant_head (
    tenant_id TEXT PRIMARY KEY,
    tenant_seq INTEGER NOT NULL CHECK (tenant_seq >= 1),
    head_hash TEXT NOT NULL
) STRICT;
INSERT INTO "tenant_head" VALUES('tnt_dev01_019e7044880000000000000000000000',20,'sha256:5abb463ba160046316639d0227afc2969ec6249c85e63cd32b96ce9e70600310');
CREATE INDEX reservations_by_step ON reservations (step_id);
CREATE INDEX reservations_by_run ON reservations (run_id);
CREATE INDEX reservations_by_task ON reservations (task_id);
COMMIT;
