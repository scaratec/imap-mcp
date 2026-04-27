# Builds a Dovecot image that has the BDD fixture config baked in.
# The upstream dovecot/dovecot image mounts /etc/dovecot read-only at
# runtime on some hosts, which breaks file-level bind mounts; baking
# the config in sidesteps the problem entirely.

FROM dovecot/dovecot:2.3

ARG USER_FILE
COPY conf/dovecot.conf /etc/dovecot/dovecot.conf
COPY users/${USER_FILE} /etc/dovecot/users
RUN chown root:dovecot /etc/dovecot/users && chmod 0640 /etc/dovecot/users
