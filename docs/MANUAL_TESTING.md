# Manual Discord testing checklist

Automated tests cover rules, persistence, ownership and the command callbacks
with mocked interactions. These steps cover what only a real Discord client can
show: modal rendering, ephemeral visibility, autocomplete and button behaviour.

**Setup:** a private test server, `DISCORD_GUILD_ID` set to it, two Discord
accounts (A and B), and a fresh database (`COC_DB_PATH=data/test.sqlite3`).

## Creation and editing (account A)
- [ ] `/character create` opens a form; submit name "Ada", occupation, age 31. The reply is visible only to A, shows a Draft sheet and the editor buttons.
- [ ] Submitting age `abc` shows a clear error and creates nothing.
- [ ] **STR·CON·SIZ·DEX·APP** and **INT·POW·EDU·Luck** forms pre-fill current values; saving updates the sheet; HP/MP/SAN/DB/Build/MOV appear once their inputs exist.
- [ ] **Roll characteristics** on a sheet with values asks for a second click, then fills all nine values (multiples of 5, SIZ/INT/EDU ≥ 40).
- [ ] **Skill**: "Spot Hidden" 70, then "侦查" blank → skill removed.
- [ ] **HP·MP·SAN·Luck**: HP above max is rejected; blank HP resets to max.
- [ ] **Finish basic sheet** is disabled until all characteristics and Luck are set; afterwards the status reads "Basic sheet complete", the message says full CoC 7e creation is not checked, and Roll/Finish buttons disappear.
- [ ] `/character create` "Harvey" — second investigator; "ada" as a name is rejected as a duplicate.

## Selection and checks
- [ ] `/character list` shows both, ⭐ on Ada (auto-selected as the first one), and a line saying Ada is chosen for this server. Selecting Harvey from the menu opens his editor.
- [ ] `/character select` autocompletes only A's investigators; select Harvey → `/check listen` footer shows "Harvey · A".
- [ ] `/check spot hidden` uses the trained value; `/check dodge` uses DEX/2; `/check STR`, `/check luck`, `/check SAN`, `/check 力量` work.
- [ ] `/check` autocomplete lists the active investigator's trained skills first.
- [ ] `/check 60 bonus_penalty:2` and `-1` show candidate rolls; `/check 150` errors privately.
- [ ] In a second server (or DM) with no selection there, `/check dodge` asks A to select an investigator; `/character select … everywhere:True` sets a default that then works there.
- [ ] Back in the first server, `/character select Bram everywhere:True` says the server's own choice still applies; `/check listen` still uses the server choice; `/character unselect` then switches this server to the default.

## Two users
- [ ] Account B creates "Bram" and selects him. A's and B's `/check listen` show their own investigator names.
- [ ] B's `/character select` / `/character view` autocomplete never shows A's investigators, and typing `Ada` returns "no investigator named". (Panels are ephemeral, so cross-user clicks are covered by the automated tests.)

## Lifecycle and persistence
- [ ] Archive Harvey (two clicks) → disappears from list; `/check` falls back to the next scope or base values.
- [ ] `/character restore Harvey` works; `/character delete` on a live investigator is refused; on an archived one asks for confirmation.
- [ ] Restart the bot: investigators, skills and selections are unchanged; startup log reports "0 migration(s) applied".
- [ ] `/character view public:True` posts the sheet visibly to the channel.
- [ ] Archive a draft whose characteristics are all filled in but which was never finished; restore it: it is still a Draft.
- [ ] With 11+ investigators (archived count too), `/character list show_archived:True` shows page buttons; Next/Previous work and nothing is cut off.
