"""Offline schema policy compiler. Does not read a database or credentials."""
import hashlib, json
from copy import deepcopy
# A single editorial security map, used for both environments. Schemas come only from
# the pinned release. None of these names grants permission merely by appearing here.
OPERATIONS={
 "memory_artifact":"sensitive_read", "memory_capture_message":"ingest",
 "memory_history":"sensitive_read", "memory_ingest_register":"ingest",
 "memory_ingest_report":"ingest", "memory_model_call":"sensitive_read",
 "memory_process":"process", "memory_provenance":"baseline_read",
 "memory_query":"generate", "memory_run_event":"sensitive_read",
 "memory_run_history":"sensitive_read", "memory_session_messages":"sensitive_read",
 "memory_session_outputs":"sensitive_read", "memory_sessions":"sensitive_read",
 "memory_source":"sensitive_read", "memory_status":"baseline_read",
 "memory_store_artifact":"ingest",
}
EFFECTS=frozenset(('memory_capture_message','memory_process'))
QUERY="memory_query"
SEARCH="memory_search"
READ=frozenset(("baseline_read","sensitive_read"))
# Shared host narrowing that reflects the approved two-call TEST slice.
# One source for both worker metadata and upstream validator; no separate copies.
NARROW={
 "memory_status": {"section":{"enum":["overview","transport"]},"offset":{"minimum":0,"maximum":1000000},"limit":{"minimum":1,"maximum":100}},
 "memory_provenance": {"record_ids":{"minItems":1,"maxItems":100,"items":{"type":"string","minLength":1,"maxLength":160,"pattern":r"^[^\u0000-\u001f]+$"}}},
}
def encoded(v):
 return json.dumps(v, ensure_ascii=True, sort_keys=True, separators=(",",":")).encode("ascii")
def digest(v):
 return hashlib.sha256(encoded(v)).hexdigest()
def compile_catalog(schema_doc, authority, *, include_effects=False, require_functional=False):
 """authority must be established by CI against an exact release, not by operator/gateway input."""
 if type(schema_doc) is not dict or schema_doc.get("revision")!=authority["revision"] or \
    schema_doc.get("module_sha256")!=authority["module_sha256"] or (
      schema_doc.get("runtime_operation_registry")!=authority["registry"] if "registry" in authority
      else schema_doc.get("runtime_mcp_sdk")!=authority["sdk"]):
  raise ValueError("Untrusted release schema metadata")
 schemas=schema_doc.get("tools")
 expected=set(OPERATIONS)
 if require_functional:expected.add(SEARCH)
 if type(schemas) is not dict or set(schemas)!=expected:
  raise ValueError("Unclassified canonical schema drift")
 if digest(schemas)!=authority["schema_sha256"]:
  raise ValueError("Release schema digest mismatch")
 descriptions=schema_doc.get("descriptions")
 if descriptions is not None and (type(descriptions) is not dict or set(descriptions)!=expected
    or any(type(value) is not str or not 0<=len(value)<=4096 for value in descriptions.values())):
  raise ValueError("Invalid canonical descriptions")
 entries={}
 classes=OPERATIONS|({SEARCH:'baseline_read'} if require_functional else {})
 for name, classification in sorted(classes.items()):
  pristine=deepcopy(schemas[name])
  schema=deepcopy(pristine)
  if schema.get("type")!="object" or not isinstance(schema.get("properties"),dict):
   raise ValueError("Unexpected tool schema")
  if name in NARROW:
   if set(NARROW[name])-set(schema["properties"]) or set(schema.get("required",()))-set(NARROW[name]):
    raise ValueError("Canonical input schema changed")
   for field, rule in NARROW[name].items():
    base=schema["properties"][field]
    if "enum" in rule and set(rule["enum"])-set(base.get("enum",())):
     raise ValueError("Cannot broaden canonical enum")
    if "items" in rule and rule["items"]["type"]!=base.get("items",{}).get("type"):
     raise ValueError("Canonical item type changed")
   schema["properties"]={k:{**schema["properties"][k],**v} for k,v in NARROW[name].items()}
  schema["additionalProperties"]=False
  projection={"baseline_read":schema} if name in NARROW else {}
  if require_functional and name==SEARCH:
   projection["baseline_read"]=schema
  if require_functional and include_effects and name==QUERY:
   projection["generate"]=schema
  if include_effects and name in EFFECTS:
   projection["intake_process"]=schema
  entries[name]={"classification":classification,"canonicalInputSchema":pristine,"capability_projections":projection,"readOnlyHint":classification in READ}
  if descriptions is not None: entries[name]["description"]=descriptions[name]
 document={"version":1,"canonical_revision":authority["revision"],"canonical_schema_hash":digest(schemas),"tools":entries}
 return {"catalog":document,"catalog_sha256":digest(document)}

def authorized(profile, catalog, lane, operation, credential_capability):
 """Pure authority check, before any upstream activity. Request cannot construct the profile."""
 if (type(profile) is not dict or lane!=profile.get("lane") or lane not in ("test","prod")
    or profile.get("catalog_sha256")!=catalog.get("catalog_sha256") or
    digest(catalog.get("catalog"))!=catalog.get("catalog_sha256")): return False
 entry=catalog.get("catalog",{}).get("tools",{}).get(operation)
 if not entry or credential_capability not in entry.get("capability_projections",{}): return False
 if credential_capability=="generate" and operation!=QUERY:return False
 if credential_capability=="intake_process" and operation not in EFFECTS:return False
 return credential_capability in profile.get("enabled_capabilities",())
