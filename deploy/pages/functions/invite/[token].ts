import type { Env } from '../../src/env';
import { handleInvite } from '../../src/invite';

export const onRequest: PagesFunction<Env, 'token'> = (ctx) =>
  handleInvite(ctx.request, ctx.env, String(ctx.params.token));
