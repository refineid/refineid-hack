# refineid-focus.pl - Auto-focus #refineid and suppress startup noise
use strict;
use warnings;
use Irssi;

our $VERSION = "1.0";
our %IRSSI = (
    authors     => "Petri Koistinen",
    contact     => q(petri.koistinen@refineid.fi),
    name        => "refineid-focus",
    description => "Automatically focuses #refineid and cleans window on connect",
    license     => "BSD",
);

sub sig_channel_sync {
    my ($channel) = @_;
    if (lc($channel->{name}) eq "#refineid") {
        $channel->window()->set_active();
        Irssi::command("^clear");
    }
}

Irssi::signal_add("channel sync", "sig_channel_sync");
Irssi::signal_add("channel joined", "sig_channel_sync");
