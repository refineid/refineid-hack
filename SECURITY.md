# Security policy for this repository

This repository is a scratchpad: shell experiments, one-off scripts and
research probes written to try something out, not software anyone ships
or relies on. Take everything here with a pinch of salt. A script may be
unfinished, may encode an assumption that was later shown wrong, or may
have been written for one machine on one afternoon.

Reports are welcome all the same. If something here is wrong or unsafe,
open a public issue and say what you found, with the file and line and a
concrete scenario. There is no embargo and no private channel, because
nothing in this repository guards anything: a finding here does not
reach a RefineID user.

Code here may do what the RefineID applications forbid. That is what this
repository is for, so a report that it does so is not a finding. The exception
is a PIN or PUK: code that prints or logs one, sends it to another machine or
service, writes it to a file or shell history, or commits it is a finding here
too. The README says so under "Rules of this repository".

What a report should not contain, here as anywhere: a real PIN, PUK or
CAN, a private key, a full personal certificate, a personal identity
code, or an unredacted log. Those identify a real person and cannot be
unpublished.

For the software people actually run, see the security policy of the
repository in question: `refineid-core`, `refineid-unix`,
`refineid-windows`, `refineid-android` or `refineid-apple`.
