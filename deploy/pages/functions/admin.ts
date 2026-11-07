import { handleAdminPage } from '../src/admin/page';
import type { Env } from '../src/env';

export const onRequest: PagesFunction<Env> = (ctx) => handleAdminPage(ctx.request, ctx.env);
