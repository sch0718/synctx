import type { Env } from '../src/env';
import { handleSettings } from '../src/settings';

export const onRequest: PagesFunction<Env> = (ctx) => handleSettings(ctx.request, ctx.env);
