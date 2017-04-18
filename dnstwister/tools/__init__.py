"""Generic tools."""
import base64
import binascii
import os
import re
import random
import socket
import string
import urlparse

import dns.resolver
import flask
import requests

from dnstwister import app
from dnstwister import cache
from dnstwister.tools import tld_db
import dnstwister.dnstwist as dnstwist


RESOLVER = dns.resolver.Resolver()
RESOLVER.lifetime = 0.1
RESOLVER.timeout = 0.1

GOOGLEDNS = 'https://dns.google.com/resolve?name={}'
GOOGLEDNS_SUCCESS = 0
GOOGLEDNS_A_RECORD = 1


def fuzzy_domains(domain):
    """Return the fuzzy domains."""
    fuzzer = dnstwist.DomainFuzzer(domain)
    fuzzer.fuzz()
    return list(fuzzer.domains)


def analyse(domain):
    """Analyse a domain."""
    data = {'fuzzy_domains': []}
    results = fuzzy_domains(domain)

    if len(results) == 0:
        return None

    # Add a hex-encoded version of the domain for the later IP resolution. We
    # do this because the same people who may use this app already have
    # blocking on things like www.exampl0e.com in URLs...
    for result in results:
        result['hex'] = binascii.hexlify(result['domain-name'])
    data['fuzzy_domains'] = results

    return (domain, data)


def parse_post_data(post_data):
    """Parse post data to return a set of domain candidates."""
    data = re.sub(r'[\t\r ]', '\n', post_data)

    # Filter out blank lines, leading/trailing whitespace
    data = filter(
        None, map(string.strip, data.split('\n'))
    )

    # Remove HTTP(s) schemes and trailing slashes.
    data = [re.sub('(^http(s)?://)|(/$)', '', domain, re.IGNORECASE)
            for domain
            in data]

    # Strip leading/trailing whitespace again.
    data = filter(None, map(string.strip, data))

    # Make all lower-case
    data = map(string.lower, data)

    return data


def parse_domain(encoded_domain):
    """Given a plain, b64- or hex-encoded string, try to decode and validate
    it and if it is valid, return it.

    Return None on un-decodable or invalid domain.
    """
    decoders = (
        str,  # Plain text (breaks on a lot of firewalls).
        binascii.unhexlify,  # The current hex-encoding scheme.
        base64.b64decode,  # The predecessor to the hex version.
    )

    for decoder in decoders:
        try:
            decoded = decoder(encoded_domain)
            if dnstwist.validate_domain(decoded):
                return decoded.lower()
        except:
            pass


def suggest_domain(search_terms):
    """Suggest a domain based on the search fields."""

    # Check for a simple common typo first - putting comma instead of period
    # in-between the second- and top-level domains.
    if len(search_terms) == 1:
        candidate = re.sub(r'[,/-]', '.', search_terms[0])
        if dnstwist.validate_domain(candidate):
            return candidate

    # Pick up space-separated domain levels.
    if len(search_terms) == 2 and search_terms[1] in tld_db.TLDS:
        candidate = '.'.join(search_terms)
        if dnstwist.validate_domain(candidate):
            return candidate

    # Attempt to make a domain from the terms.
    joiners = ('', '-') # for now, also trialling ('', '-', '.')
    tlds = ('com',)  # for now
    suggestions = []

    # Filter out a ton of garbage being submitted
    if len(search_terms) > 2:
        return

    # Filter out long words
    search_terms = [term
                    for term
                    in search_terms
                    if len(term) < 30]

    # Filter out silly characters
    search_terms = [re.sub(r'[^a-zA-Z0-9\-]', '', term)
                    for term
                    in search_terms]

    # Join the terms
    for joiner in joiners:
        suggestions.append(joiner.join(search_terms))

    # Add TLDs
    suggested_domains = []
    for tld in tlds:
        suggested_domains.extend(['{}.{}'.format(s.lower(), tld)
                                  for s
                                  in suggestions])

    # Drop out duplicates
    suggested_domains = list(set(suggested_domains))

    # Filter for those that are actually valid domains
    valid_suggestions = filter(
        dnstwist.validate_domain, suggested_domains
    )

    if len(valid_suggestions) == 0:
        return

    return random.choice(valid_suggestions)


def is_valid_ip(ip_string):
    """Matches valid ipv4 IP addresses."""
    try:
        socket.inet_aton(ip_string)
        return True
    except socket.error:
        return False


@cache.memoize(3600)
def resolve(domain):
    """Resolves a domain to an IP.

    Returns and (IP, False) on successful resolution, (False, False) on
    successful failure to resolve and (None, True) on error in attempting to
    resolve.

    Cached to 1 hour.
    """
    if dnstwist.validate_domain(domain) is None:
        return False, True

    # Try for an 'A' record.
    try:
        ip_addr = str(sorted(RESOLVER.query(domain, 'A'))[0].address)
        return ip_addr, False
    except:
        pass

    # Try for a simple resolution if the 'A' record request failed
    try:
        ip_addr = socket.gethostbyname(domain)
        # Weird-ass edge case that sometimes happens?!?!
        if ip_addr != '127.0.0.1':
            return ip_addr, False
    except:
        pass

    return google_resolve(domain)


def google_resolve(domain):
    """Google's Public DNS resolver."""
    try:
        response = requests.get(GOOGLEDNS.format(domain)).json()
        if response['Status'] == GOOGLEDNS_SUCCESS:
            if 'Answer' in response.keys():
                answer = response['Answer'][0]
                if answer['type'] == GOOGLEDNS_A_RECORD:
                    ip_addr = answer['data']
                    if is_valid_ip(ip_addr):
                        return ip_addr, False
        return False, False
    except Exception as ex:
        app.logger.error(
            'Failed to resolve IP via Google Public DNS: {}'.format(ex)
        )
        # TODO: Remove this. As this is under evaluation, showing the user
        # errors in domain resolution when it may just be a rate limit I've
        # hit isn't helpful. Testing will show what errors I can ignore or not
        # and I'll refine the exceptions then.
        return False, False
        #return False, True


def random_id(n_bytes=32):
    """Generate a random id for an email subscription (for instance)."""
    return binascii.hexlify(os.urandom(n_bytes))


def api_url(view, var_pretty_name):
    """Create nice API urls with place holders."""
    view_path = '.{}'.format(view.func_name)
    route_var = view.func_code.co_varnames[:view.func_code.co_argcount][0]
    path = flask.url_for(view_path, **{route_var: ''})
    path += '{' + var_pretty_name + '}'
    return urlparse.urljoin(
        flask.request.url_root,
        path
    )
