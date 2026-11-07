import { handleAuth } from '../../src/auth';
import type { Env } from '../../src/env';

export const onRequest: PagesFunction<Env> = (ctx) => handleAuth(ctx.request, ctx.env);
