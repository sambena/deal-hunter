// Deal Hunter's own API. Each route says what it needs in its config:
//   signedIn: true     a signed-in person (browser or paired phone), or 401 signed_out
//   browserOnly: true  never a paired phone (403 bearer_not_allowed)
// Every POST, PUT, PATCH and DELETE needs X-Cave-Request: 1 (the UI's api() sends it). Check request bodies with zod;
// throw new Refusal(status, code, message) for anything the person should see as {error, message}.
// The Python app's routes (../server.py) are ported here in their own PR (#457, brief: delivery step 4).
import type { FastifyInstance } from 'fastify';

export async function routes(_app: FastifyInstance): Promise<void> {}
