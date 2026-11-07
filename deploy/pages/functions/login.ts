import { handleLogin } from '../src/auth';
import type { Env } from '../src/env';

export const onRequestGet: PagesFunction<Env> = (ctx) => handleLogin(ctx.request, ctx.env);
