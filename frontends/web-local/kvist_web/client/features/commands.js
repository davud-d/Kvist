// The "/" commands of the input. Pure: what the text means; the menu and the actions live in slash.js and app.js.
export const COMMANDS = [
    { name: 'model', aliases: ['models'], description: 'Change the model for this session and new ones' },
    { name: 'reasoning', aliases: ['effort'], description: 'Change how much the model thinks before it acts' },
    { name: 'role', description: 'Change what Kvist may do in your workspace' },
    { name: 'new', description: 'Start a new session; this one stays in the sidebar' },
    { name: 'provider', description: 'Add a provider or switch to another one' },
    { name: 'diff', description: 'Show the changes Kvist made in this session' },
    { name: 'files', description: 'Show the files Kvist read and changed' },
    { name: 'stop', description: 'Stop the current task' },
];
// A command typed with a Russian layout on: the same keys give Cyrillic letters ("/ьщвуд" is "/model").
const CYRILLIC = 'йцукенгшщзфывапролдячсмить', LATIN = 'qwertyuiopasdfghjklzxcvbnm';
const latin = text => [...text.toLowerCase()].map(c => LATIN[CYRILLIC.indexOf(c)] ?? c).join('');
const names = command => [command.name, ...(command.aliases || [])];
// The menu opens while the text is "/" and one word: a slash inside a message or a path opens nothing.
export function slashQuery(text) {
    const match = /^\/([^\s/]*)$/.exec(text);
    return match ? latin(match[1]) : null;
}
export function matchCommands(query, commands = COMMANDS) {
    return commands.filter(command => names(command).some(name => name.startsWith(query)));
}
// A message that starts with "/word" (a word, then a space or the end, no second slash) is a command attempt
// and never goes to the agent: { command } when it is known, { unknown: "/word" } when it is not. Anything
// else, a path such as "/tmp/a is broken" included, is an ordinary message (null).
export function parseCommand(text, commands = COMMANDS) {
    const match = /^\/([^\s/]+)(?:\s|$)/.exec(text.trim());
    if (!match)
        return null;
    const word = latin(match[1]);
    const command = commands.find(item => names(item).includes(word));
    return command ? { command } : { unknown: `/${match[1]}` };
}
