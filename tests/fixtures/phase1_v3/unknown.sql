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
INSERT INTO "audit_log" VALUES(2,1800000000000,'GRANT_INSTALLED','{"run":"run_dev01_01a21fb5994cd0b5fc6a24962909646d"}','ALLOW',NULL,'policy-1',0,'sha256:68b55d70e46e436f4f022dd65651f128910360b6ddfcf5baf8df696d3e31376d','sha256:7c966f8490e219793b24fa475f73edabaf587d93d033c0feedf7bd9748256111');
INSERT INTO "audit_log" VALUES(3,1800000000000,'POLICY_ALLOWED','{"attempt":"att_dev01_01a21fb599542f0c323812e312c771b4"}','ALLOW',NULL,'policy-1',0,'sha256:7c966f8490e219793b24fa475f73edabaf587d93d033c0feedf7bd9748256111','sha256:e831ad6e7541baa3ce8267fd22e752ee66dff66a16ad68264dce4320e3ddb09c');
INSERT INTO "audit_log" VALUES(4,1800000000000,'PERMIT_ISSUED','{"attempt":"att_dev01_01a21fb599542f0c323812e312c771b4","permit":"prm_dev01_019e7044880000000000000000000088"}','ALLOW',NULL,'policy-1',0,'sha256:e831ad6e7541baa3ce8267fd22e752ee66dff66a16ad68264dce4320e3ddb09c','sha256:5e0a5faa3a99c15522c1cb3440436aea22d7c8c50db524757bc3bdf4013ea0c2');
INSERT INTO "audit_log" VALUES(5,1800000000000,'PERMIT_CONSUMED','{"attempt":"att_dev01_01a21fb599542f0c323812e312c771b4","permit":"prm_dev01_019e7044880000000000000000000088","receipt":"rcp_dev01_019e704488000000000000000000008a"}','ALLOW',NULL,'policy-1',0,'sha256:5e0a5faa3a99c15522c1cb3440436aea22d7c8c50db524757bc3bdf4013ea0c2','sha256:04881bf930d55b5a83d7d92f52b186581de09ec5b18251e15ff29624be1e39e5');
INSERT INTO "audit_log" VALUES(6,1800000000000,'RECEIPT_CLAIMED','{"attempt":"att_dev01_01a21fb599542f0c323812e312c771b4","receipt":"rcp_dev01_019e704488000000000000000000008a"}','ALLOW',NULL,NULL,NULL,'sha256:04881bf930d55b5a83d7d92f52b186581de09ec5b18251e15ff29624be1e39e5','sha256:2295789f10470ad1e7fc90fdbde794e81f732bcf2429088bc3152e4653489be0');
INSERT INTO "audit_log" VALUES(7,1800000000000,'DISPATCH_STARTED','{"attempt":"att_dev01_01a21fb599542f0c323812e312c771b4","receipt":"rcp_dev01_019e704488000000000000000000008a"}','ALLOW',NULL,NULL,NULL,'sha256:2295789f10470ad1e7fc90fdbde794e81f732bcf2429088bc3152e4653489be0','sha256:ac173b98e2bb7dfbbb7467798db8fdfd902394f8b49a7e33ffaf1e69cb7a9233');
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
INSERT INTO "budget_caps" VALUES('STEP','stp_dev01_01a21fb5995187d0bc8c149a26618d62',10000);
INSERT INTO "budget_caps" VALUES('RUN','run_dev01_01a21fb5994cd0b5fc6a24962909646d',10000);
INSERT INTO "budget_caps" VALUES('TASK','tsk_dev01_01a21fb5994b9c84558205eb71781d3f',10000);
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
INSERT INTO "counter_caps" VALUES('RUN','run_dev01_01a21fb5994cd0b5fc6a24962909646d','MODEL_CALLS',100);
INSERT INTO "counter_caps" VALUES('TASK','tsk_dev01_01a21fb5994b9c84558205eb71781d3f','MODEL_CALLS',100);
INSERT INTO "counter_caps" VALUES('RUN','run_dev01_01a21fb5994cd0b5fc6a24962909646d','TOOL_CALLS',100);
INSERT INTO "counter_caps" VALUES('TASK','tsk_dev01_01a21fb5994b9c84558205eb71781d3f','TOOL_CALLS',100);
INSERT INTO "counter_caps" VALUES('RUN','run_dev01_01a21fb5994cd0b5fc6a24962909646d','RETRIES',100);
INSERT INTO "counter_caps" VALUES('TASK','tsk_dev01_01a21fb5994b9c84558205eb71781d3f','RETRIES',100);
INSERT INTO "counter_caps" VALUES('RUN','run_dev01_01a21fb5994cd0b5fc6a24962909646d','WORK_UNITS',100);
INSERT INTO "counter_caps" VALUES('TASK','tsk_dev01_01a21fb5994b9c84558205eb71781d3f','WORK_UNITS',100);
CREATE TABLE counter_usage (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    counter TEXT NOT NULL,
    used INTEGER NOT NULL CHECK (used >= 0),
    PRIMARY KEY (scope_type, scope_id, counter)
) STRICT;
INSERT INTO "counter_usage" VALUES('RUN','run_dev01_01a21fb5994cd0b5fc6a24962909646d','TOOL_CALLS',1);
INSERT INTO "counter_usage" VALUES('TASK','tsk_dev01_01a21fb5994b9c84558205eb71781d3f','TOOL_CALLS',1);
INSERT INTO "counter_usage" VALUES('RUN','run_dev01_01a21fb5994cd0b5fc6a24962909646d','WORK_UNITS',1);
INSERT INTO "counter_usage" VALUES('TASK','tsk_dev01_01a21fb5994b9c84558205eb71781d3f','WORK_UNITS',1);
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
INSERT INTO "decisions" VALUES('att_dev01_01a21fb599542f0c323812e312c771b4','tnt_dev01_019e7044880000000000000000000000','run_dev01_01a21fb5994cd0b5fc6a24962909646d','sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3','rsv_dev01_019e7044880000000000000000000086','{"access_epoch":0,"apply_change":null,"args_hash":"sha256:0c7cfd7dedb9dd04fd7e05c19a28f4b346e05ea79ad9e440a2e524d5b7082bc0","attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","cost_basis":{"estimate":{"currency":"XTU","micro_units":0},"kind":"tool"},"cost_cap":{"currency":"XTU","micro_units":5000},"descriptor_version":3,"destinations":[],"effect_class":"READ_ONLY","expires_at_ms":1800000060000,"hash_scheme":"SYNTHETIC_SHA256","kind":"TOOL_CALL","model":null,"policy_version":"policy-1","request_hash":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000086","run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","sandbox_profile_hash":null,"step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62","target_resources":[],"task_id":"tsk_dev01_01a21fb5994b9c84558205eb71781d3f","tenant_id":"tnt_dev01_019e7044880000000000000000000000","tool":{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:09cc39268f898a09466880756b2f7afd81fe09a49c7114edd5210974200db5b1","tool_id":"synthetic_op","version":"1.0.0"},"workspace_ref":"sha256:20c988542b445212f959e5f1094801ce10e7cae1e7832c218e8941e3bbb84b18"}','{"approval_ref":"sha256:e62ef87580cf4336a581d63305583a27edc96c4a48f2f5bffec9d238981714ce","artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest":{"approval_conditions":[],"cost_estimate":{"currency":"XTU","micro_units":0},"destinations":[],"effect_class":"READ_ONLY","external_guarantee":"NONE","input_schema":{"fields":[{"apply":null,"max_length":256,"maximum":null,"minimum":null,"name":"value","required":true,"resource":null,"type":"STRING"}],"max_bytes":1024},"kind":"BUILTIN","manifest_hash":"sha256:09cc39268f898a09466880756b2f7afd81fe09a49c7114edd5210974200db5b1","max_output_bytes":70000,"output_schema":{"fields":[{"apply":null,"max_length":65536,"maximum":null,"minimum":null,"name":"content","required":true,"resource":null,"type":"STRING"},{"apply":null,"max_length":null,"maximum":null,"minimum":null,"name":"truncated","required":true,"resource":null,"type":"BOOLEAN"}],"max_bytes":70000},"required_permissions":[],"resources":[],"sandbox_profile":null,"timeout_ms":5000,"tool_id":"synthetic_op","version":"1.0.0"}}','sha256:f84644404c9828a0d5cbd5d8d99ce0e3ad906078644e0e8e7b644a03a585b2a1',0,NULL);
CREATE TABLE deny_list (
    tenant_id TEXT NOT NULL,
    path TEXT NOT NULL,
    PRIMARY KEY (tenant_id, path)
) STRICT;
CREATE TABLE entity_times (
    entity_id TEXT PRIMARY KEY,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT;
INSERT INTO "entity_times" VALUES('tsk_dev01_01a21fb5994b9c84558205eb71781d3f',1800000000000);
INSERT INTO "entity_times" VALUES('run_dev01_01a21fb5994cd0b5fc6a24962909646d',1800000000000);
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
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',1,'evt_dev01_01a21fb5994b7c99f331662be40bde5a',NULL,NULL,1,'sha256:dddff2b03ec2b2ccc73e8ff8b314367385597982b74647d4badef00da7b79fe6','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5994b7c99f331662be40bde5a","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"CREATE","after":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":[],"status":"CREATED","task_id":"tsk_dev01_01a21fb5994b9c84558205eb71781d3f"},"before":null,"entity":"task"}]},"policy_version":"policy-1","prev_hash":null,"recorded_at_ms":1795828324683,"refs":[],"revocation_gen":0,"run_id":null,"run_seq":null,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":1,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',2,'evt_dev01_01a21fb5994c17af5a5098ba91b63a87','run_dev01_01a21fb5994cd0b5fc6a24962909646d',1,1,'sha256:b023b929b058ef1c1260b7a52476f8505eb30665ca2047d3601230074da70f4a','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5994c17af5a5098ba91b63a87","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":[],"status":"ACTIVE","task_id":"tsk_dev01_01a21fb5994b9c84558205eb71781d3f"},"before":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":[],"status":"CREATED","task_id":"tsk_dev01_01a21fb5994b9c84558205eb71781d3f"},"entity":"task"},{"action":"CREATE","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000083","run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","status":"CREATED","task_id":"tsk_dev01_01a21fb5994b9c84558205eb71781d3f"},"before":null,"entity":"run","evidence":null},{"action":"ATTACH_RUN","after":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":["run_dev01_01a21fb5994cd0b5fc6a24962909646d"],"status":"ACTIVE","task_id":"tsk_dev01_01a21fb5994b9c84558205eb71781d3f"},"before":{"budget_total":{"currency":"XTU","micro_units":5000000},"caps_total":{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000},"done_criteria":["sha256:50f4aec0bf9b1464109102a0e38866803a40fab317b1db32312500343b0d265a"],"goal_ref":"sha256:0304523efff53f243d76dc81b7c271f922292543cead846ee714f066c3331e5f","runs":[],"status":"ACTIVE","task_id":"tsk_dev01_01a21fb5994b9c84558205eb71781d3f"},"entity":"task"}]},"policy_version":"policy-1","prev_hash":"sha256:dddff2b03ec2b2ccc73e8ff8b314367385597982b74647d4badef00da7b79fe6","recorded_at_ms":1795828324684,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":1,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":2,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',3,'evt_dev01_01a21fb5994dc8bacb94dce527636933','run_dev01_01a21fb5994cd0b5fc6a24962909646d',2,1,'sha256:7d989cb9005221122319595ed49cff90e01cefbc26121b8089efde1ab9b8be49','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5994dc8bacb94dce527636933","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000083","run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","status":"QUEUED","task_id":"tsk_dev01_01a21fb5994b9c84558205eb71781d3f"},"before":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000083","run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","status":"CREATED","task_id":"tsk_dev01_01a21fb5994b9c84558205eb71781d3f"},"entity":"run","evidence":null}]},"policy_version":"policy-1","prev_hash":"sha256:b023b929b058ef1c1260b7a52476f8505eb30665ca2047d3601230074da70f4a","recorded_at_ms":1795828324685,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":2,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":3,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',4,'evt_dev01_01a21fb5994f7b1384a097501338f887','run_dev01_01a21fb5994cd0b5fc6a24962909646d',3,1,'sha256:75f58853b80b0fccd070bdae75559b3f3691ee60efb5411c199ce06466eacdaa','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5994f7b1384a097501338f887","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000083","run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","status":"RUNNING","task_id":"tsk_dev01_01a21fb5994b9c84558205eb71781d3f"},"before":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000083","run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","status":"QUEUED","task_id":"tsk_dev01_01a21fb5994b9c84558205eb71781d3f"},"entity":"run","evidence":null}]},"policy_version":"policy-1","prev_hash":"sha256:7d989cb9005221122319595ed49cff90e01cefbc26121b8089efde1ab9b8be49","recorded_at_ms":1795828324687,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":3,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":4,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',5,'evt_dev01_01a21fb599510e792cb6822bace097d6','run_dev01_01a21fb5994cd0b5fc6a24962909646d',4,1,'sha256:f40590f75d23f054b72fdc7b141258fa95da060c438622e4f127ff3d2679cdbe','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb599510e792cb6822bace097d6","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"CREATE","after":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":4,"status":"PLANNED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"before":null,"entity":"step"}]},"policy_version":"policy-1","prev_hash":"sha256:75f58853b80b0fccd070bdae75559b3f3691ee60efb5411c199ce06466eacdaa","recorded_at_ms":1795828324689,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":4,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":5,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',6,'evt_dev01_01a21fb59952f3e8c9b9c1e4e1c81ec5','run_dev01_01a21fb5994cd0b5fc6a24962909646d',5,1,'sha256:9944d0d12f5f1769404cb73daa9ea72712725a1b4e5464809c2159b56d662dcd','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb59952f3e8c9b9c1e4e1c81ec5","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":4,"status":"IN_PROGRESS","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"before":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":4,"status":"PLANNED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"entity":"step"}]},"policy_version":"policy-1","prev_hash":"sha256:f40590f75d23f054b72fdc7b141258fa95da060c438622e4f127ff3d2679cdbe","recorded_at_ms":1795828324690,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":5,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":6,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',7,'evt_dev01_01a21fb5995579e76ea816ce4b40f947','run_dev01_01a21fb5994cd0b5fc6a24962909646d',6,1,'sha256:de4acbdba9e35cfd303ed67cbaa12655922dc7fea0826910b36c8a236fa06806','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5995579e76ea816ce4b40f947","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"CREATE","after":{"action_hash":null,"approval_required":null,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":null,"receipt_id":null,"reservation_id":null,"state":"PROPOSED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"before":null,"entity":"attempt","evidence":null,"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:9944d0d12f5f1769404cb73daa9ea72712725a1b4e5464809c2159b56d662dcd","recorded_at_ms":1795828324693,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":6,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":7,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',8,'evt_dev01_01a21fb599568e949093e4c2954e3d3a','run_dev01_01a21fb5994cd0b5fc6a24962909646d',7,1,'sha256:99f8c5e46d252a663486c37c8ebda1a1e55d40de67d98e2dc8bcad7c5d881ee9','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb599568e949093e4c2954e3d3a","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":null,"approval_required":null,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":null,"receipt_id":null,"reservation_id":null,"state":"VALIDATED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"before":{"action_hash":null,"approval_required":null,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":null,"receipt_id":null,"reservation_id":null,"state":"PROPOSED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"entity":"attempt","evidence":{"kind":"validation","schema_ref":"sha256:df0ad6e43880f09c90ebf95f19110178aba6890df0010ebda7485029e2b543b4"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:de4acbdba9e35cfd303ed67cbaa12655922dc7fea0826910b36c8a236fa06806","recorded_at_ms":1795828324694,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":7,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":8,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',9,'evt_dev01_01a21fb59957f8df0553d8c582ea9c08','run_dev01_01a21fb5994cd0b5fc6a24962909646d',8,1,'sha256:95dda5c9aa5f6fd37770e8d61a12e8f520c3825265f7d224b826001ddb1b5bc4','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb59957f8df0553d8c582ea9c08","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":null,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000086","state":"CONTENT_FIXED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"before":{"action_hash":null,"approval_required":null,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":null,"receipt_id":null,"reservation_id":null,"state":"VALIDATED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"entity":"attempt","evidence":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","kind":"content_fixed","reservation_id":"rsv_dev01_019e7044880000000000000000000086"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:99f8c5e46d252a663486c37c8ebda1a1e55d40de67d98e2dc8bcad7c5d881ee9","recorded_at_ms":1795828324695,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":8,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":9,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',10,'evt_dev01_01a21fb59959e76c524bb09724ece966','run_dev01_01a21fb5994cd0b5fc6a24962909646d',9,1,'sha256:50aff6bc00d3fb9e085ea8141cadf515cf659d125e3e74329de4399c4f6606ad','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb59959e76c524bb09724ece966","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":false,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000086","state":"POLICY_ALLOWED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"before":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":null,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000086","state":"CONTENT_FIXED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"entity":"attempt","evidence":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":false,"decision_ref":"sha256:86ae35d58a6aa3b5742df94ef9d7162219f0106a911ae1954c1f0604aaec805d","kind":"policy_decision","policy_version":"policy-1"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:95dda5c9aa5f6fd37770e8d61a12e8f520c3825265f7d224b826001ddb1b5bc4","recorded_at_ms":1795828324698,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":9,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":10,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',11,'evt_dev01_01a21fb5995e44bf0e511d453173ed82','run_dev01_01a21fb5994cd0b5fc6a24962909646d',10,1,'sha256:1d8e01e1b9b6c30cbca0dac76a07f3afe31f1108cca3607a6d02294751fc2fbb','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5995e44bf0e511d453173ed82","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":false,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000086","state":"BUDGET_RESERVED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"before":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":false,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000086","state":"POLICY_ALLOWED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"entity":"attempt","evidence":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","kind":"reservation","reservation_id":"rsv_dev01_019e7044880000000000000000000086"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:50aff6bc00d3fb9e085ea8141cadf515cf659d125e3e74329de4399c4f6606ad","recorded_at_ms":1795828324702,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":10,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":11,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',12,'evt_dev01_01a21fb59961e5b5fefcb4f5621b5b25','run_dev01_01a21fb5994cd0b5fc6a24962909646d',11,1,'sha256:5ed70f3215a1895faa9674a4f934650aa819416b357dd6c4a9825856d8f99839','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb59961e5b5fefcb4f5621b5b25","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":false,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000086","state":"INTENT_RECORDED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"before":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":false,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000086","state":"BUDGET_RESERVED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"entity":"attempt","evidence":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","intent_ref":"sha256:282bcbc3f0a34a8a4ac6f00c276fcf66cf3757a3332e83d92208e5079af46922","kind":"intent"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:1d8e01e1b9b6c30cbca0dac76a07f3afe31f1108cca3607a6d02294751fc2fbb","recorded_at_ms":1795828324705,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":11,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":12,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',13,'evt_dev01_01a21fb59963b872a123166e399834e2','run_dev01_01a21fb5994cd0b5fc6a24962909646d',12,1,'sha256:48803d7dd05cedac962ed4d9900b6f8f0dcc18bb3f0290c3a7fbb358bc08058c','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb59963b872a123166e399834e2","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":false,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":"prm_dev01_019e7044880000000000000000000088","receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000086","state":"PERMIT_ISSUED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"before":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":false,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":null,"receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000086","state":"INTENT_RECORDED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"entity":"attempt","evidence":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","kind":"permit_issued","permit_id":"prm_dev01_019e7044880000000000000000000088","reservation_id":"rsv_dev01_019e7044880000000000000000000086"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:5ed70f3215a1895faa9674a4f934650aa819416b357dd6c4a9825856d8f99839","recorded_at_ms":1795828324707,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":12,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":13,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',14,'evt_dev01_01a21fb5996613af7ecb717bdd11a633','run_dev01_01a21fb5994cd0b5fc6a24962909646d',13,1,'sha256:9687b3b4add135b49c45cc8b9fd425720a54e3309a870784fb74d83870286056','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5996613af7ecb717bdd11a633","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":false,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":"prm_dev01_019e7044880000000000000000000088","receipt_id":"rcp_dev01_019e704488000000000000000000008a","reservation_id":"rsv_dev01_019e7044880000000000000000000086","state":"PERMIT_CONSUMED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"before":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":false,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":"prm_dev01_019e7044880000000000000000000088","receipt_id":null,"reservation_id":"rsv_dev01_019e7044880000000000000000000086","state":"PERMIT_ISSUED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"entity":"attempt","evidence":{"kind":"permit_consumed","permit_id":"prm_dev01_019e7044880000000000000000000088","receipt_id":"rcp_dev01_019e704488000000000000000000008a"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:48803d7dd05cedac962ed4d9900b6f8f0dcc18bb3f0290c3a7fbb358bc08058c","recorded_at_ms":1795828324710,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":13,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":14,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',15,'evt_dev01_01a21fb5996918c78631edb64e7261d4','run_dev01_01a21fb5994cd0b5fc6a24962909646d',14,1,'sha256:1cf450c1f1066658c9ccf1be6eab8e9995d0051f99915f7ccc1d884d69a15c39','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5996918c78631edb64e7261d4","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":false,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":"prm_dev01_019e7044880000000000000000000088","receipt_id":"rcp_dev01_019e704488000000000000000000008a","reservation_id":"rsv_dev01_019e7044880000000000000000000086","state":"DISPATCH_AUTHORIZED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"before":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":false,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":"prm_dev01_019e7044880000000000000000000088","receipt_id":"rcp_dev01_019e704488000000000000000000008a","reservation_id":"rsv_dev01_019e7044880000000000000000000086","state":"PERMIT_CONSUMED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"entity":"attempt","evidence":{"kind":"dispatch","receipt_id":"rcp_dev01_019e704488000000000000000000008a","report_ref":"sha256:aba5d91e4400c071b316de280bcf740c03fe2ca8092711a6fe86ff4169b80532"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:9687b3b4add135b49c45cc8b9fd425720a54e3309a870784fb74d83870286056","recorded_at_ms":1795828324713,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":14,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":15,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',16,'evt_dev01_01a21fb5996b0f94d9a4d0f4638f69b5','run_dev01_01a21fb5994cd0b5fc6a24962909646d',15,1,'sha256:89ca56e7854b85538a863e147699c1612e43a08a7fa64c7a7564ece63268ce6a','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5996b0f94d9a4d0f4638f69b5","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":false,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":"prm_dev01_019e7044880000000000000000000088","receipt_id":"rcp_dev01_019e704488000000000000000000008a","reservation_id":"rsv_dev01_019e7044880000000000000000000086","state":"DISPATCH_STARTED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"before":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":false,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":"prm_dev01_019e7044880000000000000000000088","receipt_id":"rcp_dev01_019e704488000000000000000000008a","reservation_id":"rsv_dev01_019e7044880000000000000000000086","state":"DISPATCH_AUTHORIZED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"entity":"attempt","evidence":{"kind":"dispatch","receipt_id":"rcp_dev01_019e704488000000000000000000008a","report_ref":"sha256:b87c8a691fe5bb540e1c8452abaea3800c6c1d195e005b34501ce1e88ab172f1"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:1cf450c1f1066658c9ccf1be6eab8e9995d0051f99915f7ccc1d884d69a15c39","recorded_at_ms":1795828324715,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":15,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":16,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',17,'evt_dev01_01a21fb5996e670cee653a447fe66607','run_dev01_01a21fb5994cd0b5fc6a24962909646d',16,1,'sha256:337cc351854c7cb987c2313b10a458ba9331cb6d3ebe783ffd6234e1a0145ecc','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5996e670cee653a447fe66607","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":1,"payload":{"transitions":[{"action":"SET_STATUS","after":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":false,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":"prm_dev01_019e7044880000000000000000000088","receipt_id":"rcp_dev01_019e704488000000000000000000008a","reservation_id":"rsv_dev01_019e7044880000000000000000000086","state":"OUTCOME_UNKNOWN","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"before":{"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","approval_required":false,"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","permit_id":"prm_dev01_019e7044880000000000000000000088","receipt_id":"rcp_dev01_019e704488000000000000000000008a","reservation_id":"rsv_dev01_019e7044880000000000000000000086","state":"DISPATCH_STARTED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"entity":"attempt","evidence":{"kind":"outcome","receipt_id":"rcp_dev01_019e704488000000000000000000008a","report_ref":"sha256:3aaaacb88db7f8433d3bf5f53a582308102b3630f920a9a337e768d6e9269b33"},"late":false}]},"policy_version":"policy-1","prev_hash":"sha256:89ca56e7854b85538a863e147699c1612e43a08a7fa64c7a7564ece63268ce6a","recorded_at_ms":1795828324718,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":16,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":17,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',18,'evt_dev01_01a21fb5998402ee236f03bc599f6064','run_dev01_01a21fb5994cd0b5fc6a24962909646d',17,1,'sha256:529ca13c9b8ccffc8c23534a47067378c88a874b2eeeba26e0435d1c7287d2eb','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5998402ee236f03bc599f6064","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000083","run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","status":"RECOVERING","task_id":"tsk_dev01_01a21fb5994b9c84558205eb71781d3f"},"before":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000083","run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","status":"RUNNING","task_id":"tsk_dev01_01a21fb5994b9c84558205eb71781d3f"},"entity":"run","evidence":null}]},"policy_version":"policy-1","prev_hash":"sha256:337cc351854c7cb987c2313b10a458ba9331cb6d3ebe783ffd6234e1a0145ecc","recorded_at_ms":1795828324740,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":17,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":18,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',19,'evt_dev01_01a21fb5998df93d24f5caa734fd6889','run_dev01_01a21fb5994cd0b5fc6a24962909646d',18,1,'sha256:942288ac97b32f312dc7e5d34ed6c8a18f5e119125cdeee06c8104e3fa592b8d','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5998df93d24f5caa734fd6889","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000083","run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","status":"RECONCILIATION_REQUIRED","task_id":"tsk_dev01_01a21fb5994b9c84558205eb71781d3f"},"before":{"cancel_requested":false,"owner_epoch":1,"parent_run_id":null,"run_grant_ref":"grt_dev01_019e7044880000000000000000000083","run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","status":"RECOVERING","task_id":"tsk_dev01_01a21fb5994b9c84558205eb71781d3f"},"entity":"run","evidence":{"evidence_ref":"sha256:07d7c16403d43b21bbc633511ebe1ed3e8cfc049c727b8aa4e10c50b04df4381","kind":"reconciliation_trigger","reason":"OUTCOME_UNKNOWN"}}]},"policy_version":"policy-1","prev_hash":"sha256:529ca13c9b8ccffc8c23534a47067378c88a874b2eeeba26e0435d1c7287d2eb","recorded_at_ms":1795828324749,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":18,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":19,"type":"runtime.transition"}');
INSERT INTO "events" VALUES('tnt_dev01_019e7044880000000000000000000000',20,'evt_dev01_01a21fb5998f379b32de792aeb11df9a','run_dev01_01a21fb5994cd0b5fc6a24962909646d',19,1,'sha256:e4731e8dd98eeef9f27f22347699f46a34517f88bb23813e84778adee6702ca4','{"access_epoch":0,"engine":{"engine":"runtime","version":"0.1.0"},"event_id":"evt_dev01_01a21fb5998f379b32de792aeb11df9a","keyed_hashes":[],"labels":["synthetic"],"owner_epoch":2,"payload":{"transitions":[{"action":"SET_STATUS","after":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":4,"status":"RECONCILIATION_REQUIRED","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"before":{"kind":"TOOL_CALL","run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":4,"status":"IN_PROGRESS","step_id":"stp_dev01_01a21fb5995187d0bc8c149a26618d62"},"entity":"step"}]},"policy_version":"policy-1","prev_hash":"sha256:942288ac97b32f312dc7e5d34ed6c8a18f5e119125cdeee06c8104e3fa592b8d","recorded_at_ms":1795828324751,"refs":[],"revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","run_seq":19,"schema_version":3,"tenant_id":"tnt_dev01_019e7044880000000000000000000000","tenant_seq":20,"type":"runtime.transition"}');
CREATE TABLE intents (
    attempt_id TEXT PRIMARY KEY,
    action_hash TEXT NOT NULL,
    reservation_id TEXT NOT NULL,
    intent_ref TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms >= 0)
) STRICT;
INSERT INTO "intents" VALUES('att_dev01_01a21fb599542f0c323812e312c771b4','sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3','rsv_dev01_019e7044880000000000000000000086','sha256:282bcbc3f0a34a8a4ac6f00c276fcf66cf3757a3332e83d92208e5079af46922',1800000000000);
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
INSERT INTO "ledger_entries" VALUES(1,'rsv_dev01_019e7044880000000000000000000086','STATE','ID_ALLOCATED',0,0,0,NULL,NULL,NULL,1800000000000);
INSERT INTO "ledger_entries" VALUES(2,'rsv_dev01_019e7044880000000000000000000086','STATE','RESERVED',1000,0,0,NULL,NULL,NULL,1800000000000);
INSERT INTO "ledger_entries" VALUES(3,'rsv_dev01_019e7044880000000000000000000086','STATE','CONSUMED',1000,0,0,NULL,NULL,NULL,1800000000000);
INSERT INTO "ledger_entries" VALUES(4,'rsv_dev01_019e7044880000000000000000000086','STATE','ACTIVE',1000,0,0,NULL,NULL,NULL,1800000000000);
INSERT INTO "ledger_entries" VALUES(5,'rsv_dev01_019e7044880000000000000000000086','STATE','UNKNOWN',1000,0,0,NULL,NULL,NULL,1800000000000);
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
INSERT INTO "permits" VALUES('prm_dev01_019e7044880000000000000000000088','att_dev01_01a21fb599542f0c323812e312c771b4','tnt_dev01_019e7044880000000000000000000000','run_dev01_01a21fb5994cd0b5fc6a24962909646d','{"access_epoch":0,"action_hash":"sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3","attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","consumer":"BROKER","expires_at_ms":1800000030000,"issued_at_ms":1800000000000,"owner_epoch":1,"permit_id":"prm_dev01_019e7044880000000000000000000088","permit_version":1,"policy_version":"policy-1","reservation_id":"rsv_dev01_019e7044880000000000000000000086","revocation_gen":0,"run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","tenant_id":"tnt_dev01_019e7044880000000000000000000000"}','d23e847ec146a3107690c69004f6cddd5ae7ad99d07617f650fed0c2c298d104','CONSUMED');
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
INSERT INTO "receipts" VALUES('rcp_dev01_019e704488000000000000000000008a','prm_dev01_019e7044880000000000000000000088','att_dev01_01a21fb599542f0c323812e312c771b4','tnt_dev01_019e7044880000000000000000000000','run_dev01_01a21fb5994cd0b5fc6a24962909646d','{"attempt_id":"att_dev01_01a21fb599542f0c323812e312c771b4","consumer":"BROKER","expires_at_ms":1800000030000,"owner_epoch":1,"pep_instance_id":"pep_dev01_019e7044880000000000000000000081","permit_id":"prm_dev01_019e7044880000000000000000000088","receipt_id":"rcp_dev01_019e704488000000000000000000008a","receipt_version":1,"reservation_id":"rsv_dev01_019e7044880000000000000000000086","revocation_gen":0,"tenant_id":"tnt_dev01_019e7044880000000000000000000000"}','USED');
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
INSERT INTO "reservations" VALUES('rsv_dev01_019e7044880000000000000000000086','att_dev01_01a21fb599542f0c323812e312c771b4','tnt_dev01_019e7044880000000000000000000000','usr_dev01_019e7044880000000000000000000001','tsk_dev01_01a21fb5994b9c84558205eb71781d3f','run_dev01_01a21fb5994cd0b5fc6a24962909646d','stp_dev01_01a21fb5995187d0bc8c149a26618d62','synthetic-2026-11',NULL,'TOOL_CALL','UNKNOWN','sha256:782f2590460b41f5f3df73ec62ba3029f9dffa44df4f8aedc398a9a563a938b3',5000,0,1000,1000,0,0,0,0,1800000000000,1800000060000,'prm_dev01_019e7044880000000000000000000088','rcp_dev01_019e704488000000000000000000008a');
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
INSERT INTO "rt_attempts" VALUES('att_dev01_01a21fb599542f0c323812e312c771b4','stp_dev01_01a21fb5995187d0bc8c149a26618d62','OUTCOME_UNKNOWN');
CREATE TABLE rt_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    status TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL CHECK (cancel_requested IN (0, 1)),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT;
INSERT INTO "rt_runs" VALUES('run_dev01_01a21fb5994cd0b5fc6a24962909646d','tsk_dev01_01a21fb5994b9c84558205eb71781d3f','tnt_dev01_019e7044880000000000000000000000','RECONCILIATION_REQUIRED',0,1800000000000);
CREATE TABLE rt_steps (
    step_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL
) STRICT;
INSERT INTO "rt_steps" VALUES('stp_dev01_01a21fb5995187d0bc8c149a26618d62','run_dev01_01a21fb5994cd0b5fc6a24962909646d','TOOL_CALL');
CREATE TABLE rt_tasks (
    task_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    budget_micro INTEGER NOT NULL CHECK (budget_micro >= 0),
    caps TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT;
INSERT INTO "rt_tasks" VALUES('tsk_dev01_01a21fb5994b9c84558205eb71781d3f','tnt_dev01_019e7044880000000000000000000000',5000000,'{"context_rebuilds":50,"memory_operations":20,"model_calls":200,"replans":10,"retries":30,"tool_calls":300,"total_operations":600,"wall_clock_ms":7200000}',1800000000000);
CREATE TABLE run_grants (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    grant_hash TEXT NOT NULL,
    body TEXT NOT NULL
) STRICT;
INSERT INTO "run_grants" VALUES('run_dev01_01a21fb5994cd0b5fc6a24962909646d','tnt_dev01_019e7044880000000000000000000000','sha256:56ba0979838d5daa96bf03971b545c4e05a5c8f1904823efe71ab7678778b24b','{"access_epoch_at_grant":0,"experiment_id":null,"expires_at_ms":1800003600000,"files":{"read":["workspace/**"],"write":["workspace/out/**"]},"issued_at_ms":1799999999000,"max_cost_per_action":{"currency":"XTU","micro_units":5000},"model_profiles":[{"model_id":"scripted-model-small","profile_version":"0.1.0"}],"period_id":"synthetic-2026-11","policy_version":"policy-1","run_grant_id":"grt_dev01_019e7044880000000000000000000085","run_id":"run_dev01_01a21fb5994cd0b5fc6a24962909646d","synthetic_task_ref":"sha256:18827c6386e82a4172ab53e2c6f913e3b56922f1773400afa220282b0db30e62","task_id":"tsk_dev01_01a21fb5994b9c84558205eb71781d3f","tenant_id":"tnt_dev01_019e7044880000000000000000000000","tools":[{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:0482401686134e9d52335e602be40b62a225b6c59e8f602fbcedea9456ef337f","tool_id":"costly_read","version":"1.0.0"},{"artifact_hash":"sha256:bca87f5d1d6d150cea3b0b581ac63b25033c9c7d85098bb389bd40b9fc8303c3","manifest_hash":"sha256:db941c7739d1e3a22e9e552e804d30ff2965905bf9de10c58c5efd0394250906","tool_id":"read_file","version":"1.0.0"},{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:60dda8fbcad88900228c8c3e0a15b1de11eafe88baded8708d43b0f0b0ef0933","tool_id":"synthetic_change","version":"1.0.0"},{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:aa2f09d596c1d1b35271252900340d3a2fb9916cc84ba6530b5d9c9b46861823","tool_id":"synthetic_costly","version":"1.0.0"},{"artifact_hash":"sha256:10e694539fd3be13ce20931e96147da29e398afa523219c7086aae6652c717a0","manifest_hash":"sha256:09cc39268f898a09466880756b2f7afd81fe09a49c7114edd5210974200db5b1","tool_id":"synthetic_op","version":"1.0.0"},{"artifact_hash":"sha256:bca87f5d1d6d150cea3b0b581ac63b25033c9c7d85098bb389bd40b9fc8303c3","manifest_hash":"sha256:9f171e9b16b75b7f2b2d920528d35dd8bd9ca4f247d6518b864a5cc35c3b32a6","tool_id":"write_file","version":"1.0.0"}],"user_id":"usr_dev01_019e7044880000000000000000000001"}');
CREATE TABLE run_head (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL
        REFERENCES tenant_head (tenant_id) DEFERRABLE INITIALLY DEFERRED,
    run_seq INTEGER NOT NULL CHECK (run_seq >= 1)
) STRICT;
INSERT INTO "run_head" VALUES('run_dev01_01a21fb5994cd0b5fc6a24962909646d','tnt_dev01_019e7044880000000000000000000000',19);
CREATE TABLE run_status_times (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    since_ms INTEGER NOT NULL CHECK (since_ms >= 0)
) STRICT;
INSERT INTO "run_status_times" VALUES('run_dev01_01a21fb5994cd0b5fc6a24962909646d','RECONCILIATION_REQUIRED',1800000000000);
CREATE TABLE run_windows (
    run_id TEXT PRIMARY KEY,
    max_elapsed_ms INTEGER NOT NULL CHECK (max_elapsed_ms >= 1)
) STRICT;
INSERT INTO "run_windows" VALUES('run_dev01_01a21fb5994cd0b5fc6a24962909646d',3600000);
CREATE TABLE runtime_owner (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    epoch INTEGER NOT NULL CHECK (epoch >= 0),
    instance_id TEXT NOT NULL,
    acquired_at_ms INTEGER NOT NULL CHECK (acquired_at_ms >= 0)
) STRICT;
INSERT INTO "runtime_owner" VALUES(1,2,'970fc401e706b04f103d24660682bf3b',1800000000000);
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
INSERT INTO "tenant_head" VALUES('tnt_dev01_019e7044880000000000000000000000',20,'sha256:e4731e8dd98eeef9f27f22347699f46a34517f88bb23813e84778adee6702ca4');
CREATE INDEX reservations_by_step ON reservations (step_id);
CREATE INDEX reservations_by_run ON reservations (run_id);
CREATE INDEX reservations_by_task ON reservations (task_id);
COMMIT;
