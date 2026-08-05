# Bug: `GET /api/v1/products/{id}` returns 404 for non-active products, even for product managers

**Service:** `dupli1-product`
**Component:** `product/pkg/handler` (routing / access control)
**Severity:** High — manage-web cannot open the detail page of any draft product
**Observed at:** `elug3/dupli1@72b97b9`, production (`manage.dupli1.com`), 2026-08-05

## Summary

The single-product endpoint has only a public, active-only implementation. A caller holding
`product.read` still gets `404` for a `draft` product, so manage-web lists a product but 404s
when that product is opened.

The list endpoint (`GET /api/v1/products`) already widens for managers. The detail endpoint
never got the same treatment, and that asymmetry is the bug.

## Reproduce

With a token holding `product.*` (service account `agent@dupli1.com`, `permissions: ["product.*"]`):

```bash
# 1. A draft product — present in the manager list
curl -sH "Authorization: Bearer $TOKEN" \
  'https://manage.dupli1.com/api/v1/products?status=draft&limit=100' | jq '.total'
# => 107   (includes 01KXSNBBKJ1ND7TNZARY9DFQEH)

# 2. Same product by id, same token
curl -sH "Authorization: Bearer $TOKEN" \
  https://manage.dupli1.com/api/v1/products/01KXSNBBKJ1ND7TNZARY9DFQEH
# => 404 {"error":"get active product: not found","code":404}

# 3. An active product by id, same token
curl -sH "Authorization: Bearer $TOKEN" \
  https://manage.dupli1.com/api/v1/products/01KY3N0FREVM5WM8T4QNRPY1ME
# => 200 OK
```

`?status=draft` and `?include_inactive=true` on the detail route make no difference.

**Expected:** a caller with `product.read` can fetch any product by id regardless of status.
**Actual:** `404 get active product: not found` for every non-active product, for every caller.

## Root cause

`GET /api/v1/products/{id}` is registered once, inside `RegisterRoutes`, with no auth middleware:

```go
// product/pkg/handler/handler.go:106
mux.HandleFunc("GET "+RoutePublicProduct, h.PublicGetProduct)
```

`PublicGetProduct` (`handler.go:284`) unconditionally calls the public service method, which is
hard-filtered to active in SQL:

```go
// product/pkg/service/product_search_service.go:92
func (s *ProductSearchService) GetPublicProduct(id string) (*domain.Product, error) {
	return s.store.GetActiveProduct(id)
}
```

```go
// product/pkg/infra/pg/product_store.go:650
`SELECT `+parentSelectCols+` FROM products WHERE id = $1 AND status = 'active'`
```

The authenticated handler for the same path covers **PUT and DELETE only** — there is no manager
GET-by-id anywhere:

```go
// product/pkg/handler/handler.go:158
// SingleProductHandler returns an http.Handler for PUT|DELETE /api/v1/products/{id}.
```

```go
// product/pkg/bootstrap/bootstrap.go:93,99,105-106
h.RegisterRoutes(mux)                                              // GET {id} -> public, unwrapped
mux.Handle("GET "+handler.RouteProducts,
	middleware.OptionalAuth(validator, h.SearchProductsHandler()))  // list -> OptionalAuth
mux.Handle("PUT "+handler.RouteProductByID, requirePerm(...))       // PUT only
mux.Handle("DELETE "+handler.RouteProductByID, requirePerm(...))    // DELETE only
```

Two consequences:

1. No `OptionalAuth` on the detail route, so `authjwt.FromContext` never carries claims there.
2. Even with claims, `PublicGetProduct` has no widening branch.

Contrast with the list endpoint, which does it correctly (`handler.go:201`):

```go
public := true
if claims, ok := authjwt.FromContext(r.Context()); ok && claims.HasPermission(permissions.ProductRead) {
	public = false
} else {
	delete(filter, "status")
}
```

Its doc comment already promises the behaviour the detail route lacks:
*"Public callers see active products only. Authenticated product managers see all statuses."*

## Impact

- manage-web returns 404 on the detail page for any product not `active`. With 107 of 155
  catalog products currently `draft`, most of the catalog is unopenable in the admin UI.
- Managers cannot review or verify a product before publishing it — the one workflow drafts exist for.
- Storefront behaviour is correct and must not change: anonymous callers should keep getting 404.

## Proposed fix

Mirror the list endpoint. Two small changes:

1. Mount the detail route with `OptionalAuth` in `bootstrap.go`, and drop it from `RegisterRoutes`
   (Go 1.22 `ServeMux` panics on a duplicate `GET /api/v1/products/{id}` pattern, so it must move
   rather than be registered twice):

```go
mux.Handle("GET "+handler.RouteProductByID,
	middleware.OptionalAuth(validator, http.HandlerFunc(h.PublicGetProduct)))
```

2. Widen inside `PublicGetProduct`:

```go
product, err := h.svc.GetPublicProduct(id)
if claims, ok := authjwt.FromContext(r.Context()); ok && claims.HasPermission(permissions.ProductRead) {
	product, err = h.svc.GetProduct(id)
}
```

`ProductSearchService.GetProduct` already exists and returns the same shape — `pg.GetProduct`
(`product_store.go:674`) scans the same `parentSelectCols`, calls `ListVariants` and
`EnrichFromVariants(variants, true)`, so the response is shape-compatible with the public path.
The only difference is the dropped status filter.

Worth deciding as part of the fix: whether a manager fetching a draft parent should also see
draft *variants*. `GetActiveProduct` filters variants to active in the memory store; `GetProduct`
does not. For an admin PDP, showing all variants is likely the desired behaviour.

## Test plan

- Anonymous `GET` of a draft product → `404` (unchanged).
- Anonymous `GET` of an active product → `200` (unchanged).
- `product.read` token `GET` of a draft product → `200` with variants.
- `product.read` token `GET` of an active product → `200` (unchanged).
- Token *without* `product.read` → `404` for drafts.
- Extend `product/pkg/handler/access_control_test.go`, which already builds a mux with
  `middleware.OptionalAuth` and per-permission wrapping, so the case fits the existing harness.

## Workaround until fixed

Fetch through the list endpoint, which is already auth-aware:

```
GET /api/v1/products?status=draft&limit=100
```

Or temporarily set the product `active`.

## Related observation (separate issue, unverified)

48 of 155 products are `active` although all 155 were explicitly set to `draft` on 2026-07-26:
LV 20, PRA 18, HER 8, YSL 2. All 20 Louis Vuitton products that had variant images re-uploaded
are in that set, which suggests a variant image upload or variant update may re-activate the
parent. Not confirmed — it needs a controlled write to prove — and it is independent of the 404
above.
