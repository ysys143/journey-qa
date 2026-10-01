INSERT INTO app_users VALUES
 ('ivy.nonrealton@example.com','admin','ivynonrealton'),
 ('marcus.fablewright@example.com','user','marcusfablewright'),
 ('priya.notarealsen@example.com','user','priyanotarealsen'),
 ('tomas.ficticio@example.com','user','tomasficticio'),
 ('yuki.testonaga@example.com','user','yukitestonaga');
INSERT INTO app_sessions VALUES
 ('s1','ivynonrealton','ivy.nonrealton@example.com','ivy.nonrealton@example.com','3f6c1e2a-8b4d-4c7e-9a51-0d2e7b9c4a18','provider-a'),
 ('s2','marcusfablewright','marcus.fablewright@example.com','marcus.fablewright@example.com','5c08e3b1-6a2f-4d9c-b7e4-2f1a8c6d3e57','provider-a'),
 ('s3','priyanotarealsen','priya.notarealsen@example.com','priya.notarealsen@example.com','d71b4a9e-2e5c-4f38-a6d0-9c3b8e5f1a24','provider-a'),
 ('s4','tomasficticio','tomas.ficticio@example.com','tomas.ficticio@example.com','8e2f6c3d-4b1a-4e7f-9d58-1a6c0b3e7f42','provider-a'),
 ('s5','yukitestonaga','yuki.testonaga@example.com','yuki.testonaga@example.com','1b9d7e4f-3c6a-4a2e-8f05-6d2c9a7b4e31','provider-a');
INSERT INTO app_usage_samples VALUES
 ('provider-a','3f6c1e2a-8b4d-4c7e-9a51-0d2e7b9c4a18','short','2030-01-15T14:00:00Z','ivy.nonrealton@example.com',NULL,'plan-large'),
 ('provider-a','3f6c1e2a-8b4d-4c7e-9a51-0d2e7b9c4a18','weekly','2030-01-15T14:00:00Z','ivy.nonrealton@example.com',NULL,'plan-large');
INSERT INTO app_users VALUES ('extra.person@example.com','user','extraperson');
