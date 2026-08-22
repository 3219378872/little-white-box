-- Local/dev test user. Username: admin  Password: 123456
-- bcrypt cost 10 (go-zero login uses golang.org/x/crypto/bcrypt).
-- docker-entrypoint-initdb.d only runs on an empty volume; stack.sh
-- re-applies this file on every middleware-up.

USE `xbh_user`;

UPDATE `user_profile`
SET
    `password` = '$2b$10$1sDV3VGvZANZvFYwEm1A5OkaRuZesonKQYuxIIuoP4iQ9BjNl.lcS',
    `nickname` = 'admin',
    `status` = 1
WHERE `username` = 'admin';

INSERT INTO `user_profile` (
    `id`,
    `username`,
    `password`,
    `nickname`,
    `status`,
    `favorites_visibility`
)
SELECT
    1,
    'admin',
    '$2b$10$1sDV3VGvZANZvFYwEm1A5OkaRuZesonKQYuxIIuoP4iQ9BjNl.lcS',
    'admin',
    1,
    1
WHERE NOT EXISTS (
    SELECT 1 FROM `user_profile` WHERE `username` = 'admin'
);
