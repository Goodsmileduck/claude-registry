# Durable Objects: the migrations array

> Parent: `../../SKILL.md`. Append-only, class-name driven, and easy to break.

## Table of contents

1. [The model in one paragraph](#the-model-in-one-paragraph)
2. [The five migration verbs](#the-five-migration-verbs)
3. [Tag semantics](#tag-semantics)
4. [Renames](#renames)
5. [Adding a new DO class to an existing Worker](#adding-a-new-do-class-to-an-existing-worker)
6. [Deletes (destructive)](#deletes-destructive)
7. [Cross-Worker transfers](#cross-worker-transfers)
8. [SQLite vs legacy KV backend](#sqlite-vs-legacy-kv-backend)
9. [Alarms and `ctx.storage`](#alarms-and-ctxstorage)
10. [Common error messages](#common-error-messages)

## The model in one paragraph

> SKILL.md Hard rule 2 applies. Migrations are append-only and class-name driven.

Cloudflare tracks DO classes by **name** in its persistence layer; the `migrations` array is the source-of-truth history. Past entries are immutable — to change behavior, append a new entry with a new `tag`. Deploys fail if the diff between the previous deploy's class set and the new one isn't explained by a new migration entry.

## The five migration verbs

```jsonc
"migrations": [
  {
    "tag": "v1",
    "new_classes": ["KVBackedThing"],
    "new_sqlite_classes": ["SqliteThing"]
  },
  {
    "tag": "v2",
    "renamed_classes": [{ "from": "OldName", "to": "NewName" }]
  },
  {
    "tag": "v3",
    "deleted_classes": ["Unused"]
  },
  {
    "tag": "v4",
    "transferred_classes": [
      { "from": "OldOwner", "from_script": "old-worker", "to": "TakenOver" }
    ]
  }
]
```

| Verb | What it does | Reversible? |
| --- | --- | --- |
| `new_classes` | Declares a class with the legacy KV-backed storage. | No (can `delete` later, with data loss). |
| `new_sqlite_classes` | Declares a class with the SQLite backend (`ctx.storage.sql`). | No — and you can NOT downgrade to KV-backed later. |
| `renamed_classes` | Renames an existing class. Data preserved. | Yes, by renaming back in a later migration. |
| `deleted_classes` | Drops a class and all its instances' storage. | NO — destroys data. |
| `transferred_classes` | Adopts a class previously owned by another Worker script. | One-way at migration time; can transfer onward later. |

## Tag semantics

`tag` is an arbitrary string used as a stable identifier for that migration entry. Convention: `v1`, `v2`, `v3`. Cloudflare records the highest `tag` applied to your Worker; on the next deploy it applies only entries with tags it has not seen. **Past tag entries are immutable** — editing the contents of an applied tag is a deploy-time error. To change behavior, append a NEW tag.

## Renames

The most common DO operation after the first deploy:

```jsonc
"durable_objects": {
  "bindings": [
    { "name": "ROOM", "class_name": "ChatRoomV2" }
  ]
},
"migrations": [
  { "tag": "v1", "new_sqlite_classes": ["ChatRoom"] },
  { "tag": "v2", "renamed_classes": [{ "from": "ChatRoom", "to": "ChatRoomV2" }] }
]
```

Steps:

1. Rename the class in code AND every place it's exported.
2. Update the binding's `class_name`.
3. Append a NEW migration entry with `renamed_classes`.
4. Deploy.

**Don't** edit the v1 entry to say `["ChatRoomV2"]` — that's a deploy error and would discard history anyway.

## Adding a new DO class to an existing Worker

Append the class to a new migration entry:

```jsonc
"migrations": [
  { "tag": "v1", "new_sqlite_classes": ["ChatRoom"] },
  { "tag": "v2", "new_sqlite_classes": ["RoomIndex"] }
]
```

Both `ChatRoom` and `RoomIndex` need bindings to be reachable. Adding without binding is harmless (the class is registered but inaccessible) but pointless.

## Deletes (destructive)

```jsonc
{ "tag": "v3", "deleted_classes": ["Unused"] }
```

This drops the class registration AND every existing instance's storage. **Irreversible.** Make backups (or `transferred_classes` it to a holding Worker) first if you might want it later.

You must also remove the corresponding binding from `durable_objects.bindings` in the same or a later deploy.

## Cross-Worker transfers

Moving a DO class from Worker A to Worker B:

In Worker A's next deploy, remove the binding and the class (or `deleted_classes` it AFTER B owns it).

In Worker B's wrangler config:

```jsonc
"durable_objects": {
  "bindings": [{ "name": "ROOM", "class_name": "ChatRoom" }]
},
"migrations": [
  { "tag": "v1", "transferred_classes": [
    { "from": "ChatRoom", "from_script": "worker-a", "to": "ChatRoom" }
  ]}
]
```

`from_script` is the source Worker's name. After Worker B deploys, B owns all existing instances + storage. Code for `ChatRoom` must be present in B.

## SQLite vs legacy KV backend

| | `new_sqlite_classes` | `new_classes` (KV-backed) |
| --- | --- | --- |
| Storage API | `ctx.storage.sql` + `ctx.storage` | `ctx.storage` only |
| SQL queries | Yes — full SQLite | No (build it yourself with `kv.put`/`get`) |
| Per-instance size limit | 10 GB | 128 KiB per key, no overall cap |
| Recommended for new DOs | Yes | No |
| Downgrade to KV | NO — cannot |

For new Durable Object classes always use `new_sqlite_classes` unless you have a specific reason not to. The legacy backend is staying around for compatibility but is not the recommended path.

## Alarms and `ctx.storage`

Alarms are scheduled wake-ups on a DO instance:

```typescript
export class ChatRoom extends DurableObject {
  async fetch(req: Request) {
    await this.ctx.storage.setAlarm(Date.now() + 60_000);
    return new Response("ok");
  }
  async alarm() {
    // Runs ~60s later, even if the DO was evicted in between.
  }
}
```

**Gotcha:** Setting `setAlarm` overwrites the previous alarm; there's only one per instance. To schedule N future events, store the queue yourself and re-arm in `alarm()`.

**Gotcha:** Alarms incur DO requests — they're billable wakeups.

## Common error messages

| Message | Cause | Fix |
| --- | --- | --- |
| `Cannot apply new-class migration to class 'X' that is already depended on` | Tried to declare `new_classes` for an existing class — usually after a rename in code without `renamed_classes` migration | Add a `renamed_classes` migration entry |
| `Class 'X' cannot be used as a Durable Object` | Class isn't exported, or `class_name` in binding doesn't match the export | Match the names exactly |
| `Cannot create a Durable Object with a tag 'vN' that has not been applied` | Migration tag in config but not yet deployed | Deploy; or remove the new tag and retry |
| `Cannot delete class 'X' — still referenced by binding` | `deleted_classes` without removing the binding | Remove the binding in the same deploy |
