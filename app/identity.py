"""Who is asking. App Service's sign-in (Easy Auth) checks the person with Entra ID before the request reaches the app and passes who they are
in X-MS-CLIENT-PRINCIPAL-* headers; App Service strips those headers from anything coming from outside, so on App Service they can be trusted.
Anywhere else (a run on KPrime) they are trusted only when LOCAL_SIGN_IN=1, which the recorder uses to show a signed-in screen."""


def who(headers, operators: set[str], trust_headers: bool) -> dict:
    oid = (headers.get("X-MS-CLIENT-PRINCIPAL-ID") or "").strip().lower() if trust_headers else ""
    idp = (headers.get("X-MS-CLIENT-PRINCIPAL-IDP") or "").strip().lower() if trust_headers else ""
    name = (headers.get("X-MS-CLIENT-PRINCIPAL-NAME") or "") if trust_headers else ""
    signed_in = bool(oid) and idp == "aad"
    allowed = signed_in and (not operators or oid in operators)
    return {"signed_in": signed_in, "name": name if signed_in else "", "allowed": allowed}
