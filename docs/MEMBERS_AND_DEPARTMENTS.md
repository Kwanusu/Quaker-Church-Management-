# Members, departments and Clerks

## One church per Clerk
Whoever registers a church becomes its Clerk. A Clerk can register only one church (platform admins can set up
more). After signing in, people land on their own church; the Clerk's **Manage** page only ever shows that church.

## Member register (Manage → Members)
The Clerk can add anyone in the church, **with or without an account** and **with or without a role**:

| Given when adding | What happens |
|---|---|
| Name only (phone optional) | A register entry. They can be put in departments and appointed officials. |
| Email of someone with a confirmed account | Linked to that account at once; they get an email. |
| Any other email | A register entry, and an invitation to create an account with that email. When they confirm it, they are linked automatically, keeping their role and departments. |

Church role is optional (default **Member**). Recording Clerks can see the register; only the Clerk changes it.
A register entry without an account doesn't count as a Clerk, so a church always has a Clerk who can sign in.

## Departments
Every church gets five standard departments, which the Clerk can rename (not delete), and can add its own:

- Main Office
- USFW (United Society of Friends Women)
- Quakermen
- Young Friends Programme
- Sunday Church School

Each department has:

- **People** from the member register, each with a department role: Member, Chairperson, Vice Chairperson,
  Secretary or Treasurer (one person per office).
- **Officials** (all roles except Member) manage their own department's people, as does the Clerk.
  An official doesn't need to be a church leader.
- **A private chat room** for its people (and the Clerk), under **Messages → Departments**.
- **Events**: when adding an event, choose a department (e.g. "USFW monthly meeting"). It shows on the
  department's page and is tagged on the calendar.

Pages: `/c/<church>/departments` (everyone) and `/c/<church>/departments/<department>`.

## API
- `GET/POST /api/v1/churches/<slug>/members/`, `PATCH/DELETE …/members/<id>/`, `POST …/members/<id>/invite/`
- `GET/POST /api/v1/churches/<slug>/departments/`, `PATCH/DELETE …/departments/<dept>/`
- `GET/POST …/departments/<dept>/members/`, `PATCH/DELETE …/members/<id>/`, `GET …/members/candidates/?q=`
- Events: `department` (id) on `/schedules/`, filter `?department=<dept>`
