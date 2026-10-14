# Future Purdue SSO integration

Local prototype accounts are enabled by default. Purdue SSO remains optional
and requires campus registration. No request has been submitted by this project.

## Official resources

- [Authentication options](https://www.purdue.edu/securepurdue/identity-access/authentication-options.php)
- [Shibboleth service and SSO request link](https://www.purdue.edu/securepurdue/identity-access/shibboleth-service.php)
- [SSO request questions](https://www.purdue.edu/securepurdue/identity-access/SSO_Request_FormQuestions_SAML.pdf)
- [Solution Services Review](https://www.purdue.edu/securepurdue/services/solution-services-review.php)

Purdue's published process starts with a Single Sign-on Request, followed by
an IAMO consultation. The current request document also lists a security review
requirement. Coordinate with the instructor or departmental sponsor to confirm
the applicant, hosting environment, administrators and intended pilot users.
The official Shibboleth page lists accounts@purdue.edu for questions.

## Application request draft

| Field | Proposed answer |
| --- | --- |
| Name | 480SmartStorage |
| Purpose | ENGT 480 equipment lending with separate storage and outdoor locker pickup/return |
| Hosting and URL | To be confirmed; a stable HTTPS application host is required |
| Contact and administrator | Supply the actual departmental sponsor and project administrator |
| Users and campuses | Supply the intended pilot campus, course members and designated TAs |
| Protocol | SAML 2.0, python3-saml 1.16.0 |
| Request binding | HTTP-Redirect AuthnRequest |
| Response binding | HTTP-POST |
| Entity ID | `https://YOUR-APP-HOST/auth/saml/metadata` |
| ACS / reply URL | `https://YOUR-APP-HOST/auth/saml/acs` |
| SP metadata URL | `https://YOUR-APP-HOST/auth/saml/metadata` |
| Provisioning | Create a user profile after a validated SAML sign-in |
| Permissions | Ordinary Student role by default; TA assigned by a server administrator |
| Required attribute | eduPersonPrincipalName, `urn:oid:1.3.6.1.4.1.5923.1.1.1.6` |
| Optional attributes | displayName and mail |
| Testing | Request an approved non-production application and callback domain |
| Logout | Application-only session revocation; no campus-wide Single Logout |

The repo URL is source control, not the application host. The current Student role
is a regular application role, not evidence of active student enrollment.
Apply pilot membership restrictions through IdP access policy and/or
`ALLOWED_PRINCIPALS`. Campus scopes other than `purdue.edu` require explicit review
and changes to the identity policy.

The application identifies SSO users by the signed ePPN attribute. Confirm its
stability and account reassignment policy with IAMO. For longer-lived identities,
agree a persistent NameID or targeted identifier and update provisioning before
activation. Never identify users by a transient NameID or an email form input.
Prototype identities use a separate `local:` namespace and are never merged
automatically with SSO profiles.

## Confirm signature compatibility first

Purdue's current questionnaire assumes ECDSA support. **This project has not
passed Purdue ECDSA interoperability testing.** The SP example uses RSA-SHA256
when request signing is enabled. XML assertion signature support also depends
on the installed xmlsec build. Ask IAMO to confirm the IdP signature algorithm,
SP request signing, SP certificate and metadata signing requirements.

If ECDSA request signatures are required, use an approved Shibboleth SP or a
validated compatible implementation before connecting this application. Do not
disable signature validation to make it connect. If IAMO provides OIDC instead,
the authentication module must be adapted; OIDC settings cannot be pasted into
this SAML configuration.

## Configuration after approval

1. Agree a stable HTTPS origin and a separate non-production environment.
2. Obtain verified IdP metadata from IAMO. The official request document gives
   `https://sso.purdue.edu/idp/shibboleth`; confirm the correct metadata for your
   approved environment. Extract the entity ID, HTTP-Redirect SSO endpoint and
   signing certificate. Do not guess URLs or trust arbitrary downloaded metadata.
3. Copy `saml/settings.example.json` to ignored `saml/settings.json` and replace
   every placeholder. Use the exact registered entity ID and ACS URL. Confirm
   NameIDPolicy and attribute release with IAMO.
4. If signed requests are required, generate and protect an SP private key and
   public certificate, set the `sp.privateKey` and `sp.x509cert` fields locally,
   and enable `security.authnRequestsSigned` with the approved algorithm.
   Metadata contains the public certificate only. If the whole Response must be
   signed, also enable `wantMessagesSigned`. Assertion signatures, strict mode,
   request correlation and rejection of deprecated algorithms stay mandatory.
5. Set server environment variables or an ignored `.env`:

   ```dotenv
   SECRET_KEY=REPLACE_WITH_A_RANDOM_SECRET_OF_AT_LEAST_32_BYTES
   PUBLIC_BASE_URL=https://YOUR-APP-HOST
   SAML_SETTINGS_PATH=saml/settings.json
   LOCAL_AUTH_ENABLED=false
   ALLOWED_PRINCIPALS=member1@purdue.edu,member2@purdue.edu
   ```

6. Restart the server. Publish valid HTTPS through a reverse proxy to loopback
   Waitress. Serve at the origin root, not a subpath. Export the SP metadata from
   its endpoint and provide it to IAMO. Keep private keys out of Git and chat.
7. Complete actual non-production campus login/MFA testing from the app's SSO
   button. Only a validated response creates the app session and user profile.
8. Grant designated TA roles using the server-side `set-role` command in README.

Blank `SAML_SETTINGS_PATH` leaves SSO disabled. Invalid configured settings refuse
startup. HTTP localhost is for prototype login and page previews, not production
SAML testing. SAML uses Secure, HttpOnly, SameSite=None cookies because the IdP
returns a cross-site POST. Do not weaken these flags or correlation checks.

For certificate rollover, verify IAMO's new certificate before trusting it.
The toolkit supports `idp.x509certMulti.signing` for multiple confirmed signing
certificates during a transition.

## Campus acceptance checks

- Approved identity and MFA reach the Dashboard with signed profile attributes.
- Cancelled authentication, missing ePPN or unauthorized pilot identity never
  create an authenticated app session.
- Invalid signatures, audience/destination, expiry, request ID or RelayState
  are rejected. A consumed assertion cannot establish a second session.
- Cross-site cookies preserve request correlation; expired requests require a
  new login from this application's page.
- Dashboard redirects anonymous users. Local sign-out revokes the old app
  session; the Purdue SSO session can remain active.
- Student users cannot assign themselves TA privileges.
- Validate actual algorithms, released attributes and certificate rollover with
  IAMO. Automated local tests do not imply campus approval or completed Purdue
  interoperability testing.
