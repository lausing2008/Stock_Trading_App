import type { OptionsFlow } from './api';

/** Why the Options Flow card has no result — shown instead of silently hiding the section
 *  (options review F1, 2026-10-21: a route error made the card disappear for every symbol). */
export function optionsFlowUnavailableText(flow: OptionsFlow & { last_known_chain_date?: string | null }): string {
  switch (flow.reason) {
    case 'fetch_error':
      return 'Could not be computed right now (server error). This is not the same as no options activity.';
    case 'options_feed_unavailable':
      return `The options data feed has no current chain for this symbol${flow.last_known_chain_date ? ` (last chain ${flow.last_known_chain_date})` : ''}.`;
    case 'chain_read_failed':
      return 'The option chain could not be read for any selected expiration. This is a data failure, not an absence of activity.';
    case 'no_volume':
      return 'No call or put volume in the selected expirations.';
    case 'no_options_listed':
      return 'No listed options for this symbol.';
    default:
      return `Unavailable${flow.reason ? ` (${flow.reason})` : ''}.`;
  }
}
