import assert from "node:assert/strict";
import { test } from "node:test";
import { imageGoal, parseImageGoal } from "../../js/envelope.js";

const photo = { name: "photo-a-1.jpg", data_url: "data:image/jpeg;base64,AAAA", size: 3 };

test("no photos: the words are the message, untouched", () => {
  assert.equal(imageGoal("hello", []), "hello");
  assert.deepEqual(parseImageGoal("hello"), { text: "hello", images: [] });
  assert.deepEqual(parseImageGoal('{"a": 1}'), { text: '{"a": 1}', images: [] });
});

test("photos travel in the envelope and come back out of it", () => {
  const text = imageGoal("what is this", [photo]);
  assert.ok(text.startsWith('{"type":"kvist-image-goal"'));
  assert.deepEqual(JSON.parse(text).images, [{ name: photo.name, data_url: photo.data_url }]);
  assert.deepEqual(parseImageGoal(text).images[0].data_url, photo.data_url);
});

test("the history keeps no pixels, so what this page sent is shown from memory, and only by its own name", () => {
  imageGoal("x", [photo]);
  const stored = JSON.stringify({ type: "kvist-image-goal", text: "x", images: [{ name: photo.name, data_url: "" }, { name: "other.jpg", data_url: "" }] });
  const { images } = parseImageGoal(stored);
  assert.equal(images[0].data_url, photo.data_url);
  assert.equal(images[1].data_url, "", "a photo from another visit stays a label");
});

test("a broken envelope is shown as the words it is", () => {
  assert.deepEqual(parseImageGoal('{"type":"kvist-image-goal"'), { text: '{"type":"kvist-image-goal"', images: [] });
});
