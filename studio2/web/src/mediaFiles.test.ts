import { describe, expect, it } from "vitest";
import { findPoster, mediaKind } from "./mediaFiles";

describe("media files", () => {
  it("knows pictures, video and sound by extension, any case", () => {
    expect(mediaKind("media/shot.png")).toBe("image");
    expect(mediaKind("media/loop.GIF")).toBe("image");
    expect(mediaKind("media/shot.webp")).toBe("image");
    expect(mediaKind("media/shot.avif")).toBe("image");
    expect(mediaKind("media/launch.mp4")).toBe("video");
    expect(mediaKind("clip.M4V")).toBe("video");
    expect(mediaKind("media/launch.webm")).toBe("video");
    expect(mediaKind("phone.mov")).toBe("video");
    expect(mediaKind("media/track.mp3")).toBe("audio");
    expect(mediaKind("voice.wav")).toBe("audio");
    expect(mediaKind("memo.m4a")).toBe("audio");
    expect(mediaKind("music.ogg")).toBe("audio");
    expect(mediaKind("sample.aiff")).toBe("audio");
    expect(mediaKind("sample.aif")).toBe("audio");
    expect(mediaKind("song.flac")).toBe("audio");
  });

  it("leaves other files alone", () => {
    expect(mediaKind("index.html")).toBeNull();
    expect(mediaKind("logo.svg")).toBeNull();
    expect(mediaKind("files/deck.pptx")).toBeNull();
    expect(mediaKind("notes.mp4.txt")).toBeNull();
    expect(mediaKind("mp4")).toBeNull();
  });

  it("finds the poster beside a video only when it exists", () => {
    const files = [{ path: "media/launch.mp4" }, { path: "media/launch.jpg" }, { path: "media/other.webm" }];
    expect(findPoster("media/launch.mp4", files)).toBe("media/launch.jpg");
    expect(findPoster("media/other.webm", files)).toBeNull();
    expect(findPoster("media.d/noext", files)).toBeNull();
  });
});
