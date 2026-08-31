#!/usr/bin/env node
import { notify } from './lib/decide.js';
import { classifierNoticeText } from './lib/classifier.js';

const message = classifierNoticeText();
if (message) notify(message);
process.exit(0);
