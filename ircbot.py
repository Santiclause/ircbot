#!/usr/bin/python2.7
import re
import socket
import sys
import threading
import time

class IRCChannel(object):
    def __init__(self, channel):
        self.channel = channel
        self.users = []
        self.topic = ''
        #should ban/invite masks be IRCUsers or just straight up strings?
        self.bans = []
        self.invites = []
        self.mode = ''

    def addinvite(self, user):
        if not user in self.invites:
            self.invites.append(user)

    def addmode(self, mode):
        for char in mode:
            if not char in self.mode:
                self.mode += char

    def ban(self, user):
        if not user in self.bans:
            self.bans.append(user)

    def delinvite(self, user):
        if user in self.invites:
            self.invites.remove(user)

    def delmode(self, mode):
        for char in mode:
            self.mode = self.mode.replace(char,'')

    def gettopic(self):
        return self.topic

    def getuser(self, user):
        for u in self.users:
            if u == user:
                return u
        return False

    def join(self, user):
        if not user in self.users:
            self.users.append(IRCUser(user))

    def part(self, user):
        if user in self.users:
            self.users.remove(user)

    def rename(self, user, nick):
        u = self.getuser(user)
        if u:
            u.setnick(nick)

    def settopic(self, topic):
        self.topic = topic

    def unban(self, user):
        if user in self.bans:
            self.bans.remove(user)

class IRCUser(object):
    def __init__(self, user, mode=''):
        self.mode = mode
        match = self.parseuser(user)
        self.addmode(match['mode'])
        self.nick = match['nick']
        self.user = match['user']
        self.hostmask = match['host']

    def __eq__(self, user):
        return self.parseuser(user)['nick'] == self.nick

    def __ne__(self, user):
        return self.parseuser(user)['nick'] != self.nick

    def __lt__(self, user):
        return self.parseuser(user)['nick'] > self.nick

    def __gt__(self, user):
        return self.parseuser(user)['nick'] < self.nick

    def __str__(self):
        return "{}!{}@{}".format(self.nick, self.user, self.hostmask)

    def __repr__(self):
        return "<IRCUser : {}!{}@{}>".format(self.nick, self.user, self.hostmask)

    def __getitem__(self, index):
        return str(self).__getitem__(index)

    def addmode(self, mode):
        for char in mode:
            if not char in self.mode:
                self.mode += char
    def delmode(self, mode):
        for char in mode:
            self.mode = self.mode.replace(char,'')

    def highestOp(self):
        for status in '~&@%+':
            if status in self.mode:
                return status
        return ''

    def isvoice(self):
        return '+' in self.mode

    def ishop(self):
        return '%' in self.mode

    def isop(self):
        return '@' in self.mode

    def issop(self):
        return '&' in self.mode

    def isowner(self):
        return '~' in self.mode

    def parseuser(self, user):
        #group 1 is modes, group 2 is nick, group 3 is user, and group 4 is hostmask
        retval = {}
        if isinstance(user, IRCUser):
            retval['mode'] = user.mode
            retval['nick'] = user.nick
            retval['user'] = user.user
            retval['host'] = user.hostmask
        else:
            match = re.match(r"^(.*?)([a-zA-Z^|_{}\[\]\\`][a-zA-Z0-9^|_{}\[\]\\\-`]*)(?:!(.*?)@(.*))?$", user)
            if not match:
                raise Exception('User match string failed on nick: ' + user)
            retval['mode'] = match.group(1)
            retval['nick'] = match.group(2)
            retval['user'] = match.group(3)
            retval['host'] = match.group(4)
        return retval

    def sethost(self, user):
        self.hostmask = self.parseuser(user)['host']

    def setnick(self, user):
        self.nick = self.parseuser(user)['nick']

class IRC(threading.Thread):
    def __init__(self, host, port, nick, ident, realname, print_callback=None, err=sys.stderr, debug_callback=None):
        threading.Thread.__init__(self)

        self.host = host
        self.port = port
        self.nick = nick
        self.identity = ident
        self.realname = realname
        self.print_callback = print_callback
        self.debug_callback = debug_callback
        self.err = err

        self.socket = None
        self.channels = {}
        self.hooks = {}
        self.timers = {}
        self._stop = threading.Event()
        self._connected = False
        self._reconnect_event = threading.Event()

    def _join(self, words):
        return ' '.join(words).lstrip(':')

    def _modesymbol(self, mode):
        if mode == 'h':
            return '%'
        elif mode == 'o':
            return '@'
        elif mode == 'v':
            return '+'
        elif mode == 'a':
            return '&'
        elif mode == 'q':
            return '~'
        else:
            return False

    def ban(self, user, channel, type="host"):
        if type == 'host':
            formatstr = "*!*@{host}"
        elif type == 'nick':
            formatstr = "{nick}!*@*"
        else:
            formatstr = type
        d = {'nick':user.nick, 'host':user.hostmask, 'user':user.user}
        self.send('mode {} +b {}'.format(channel, formatstr.format(**d)))

    def callhook(self, event, *args):
        if self.hooks.has_key(event):
            for hook in self.hooks[event]:
                if not hook(self,event,*args):
                    break

    def connect(self):
        self.socket = socket.socket()
        self.socket.connect((self.host, self.port))
        self.send('NICK ' + self.nick)
        self.send('USER {} {} bla :{}'.format(self.identity, self.host, self.realname))

    def debug(self, msg):
        if self.debug_callback:
            self.debug_callback(msg)

    def getchannel(self, channel):
        if not channel in self.channels:
            return False
        return self.channels[channel]

    def getnick(self):
        return self.nick

    def gettimer(self, name):
        if not name in self.timers:
            return None
        return self.timers[name]

    def hook(self, event, func, priority=False):
        #very trivial priority system.
        if not event in self.hooks:
            self.hooks[event] = []
        if not func in self.hooks[event]:
            if priority:
                self.hooks[event].insert(0, func)
            else:
                self.hooks[event].append(func)

    def kick(self, user, chan, reason=''):
        if reason:
            reason = ' :' + reason
        else:
            reason = ''
        self.send('kick {} {}{}'.format(chan, user.nick, reason))

    def msg(self, target, message):
        self.send('PRIVMSG %s :%s' % (target, message))

    def OnConnect(self):
        self._connected = True
        self.callhook('OnConnect')

    def OnJoin(self, user, channel):
        if not channel in self.channels:
            self.channels[channel] = IRCChannel(channel)
        user = IRCUser(user)
        self.getchannel(channel).join(user)
        if user == 'Bot-sama':
            self.send('MODE %s b' % channel)
        else:
            self.printline('{} has joined {}.'.format(user.nick, channel))
        self.callhook('OnJoin', user, channel)

    def OnKick(self, user, channel, target, message):
        user = IRCUser(user)
        target = IRCUser(target)
        if not channel in self.channels:
            raise Exception("Channel didn't exist for some reason.")
        self.printline('{} has kicked {} from {} ({})'.format(user.nick, target.nick, channel, message))
        self.getchannel(channel).part(target)
        self.callhook('OnKick', user, channel, target, message)

    def OnMode(self, user, target, mode, userlist):
        user = IRCUser(user)
        if target[0] == '#':
            tar = target
            users = list(userlist)
            modes = re.findall('(\+|\-)([a-zA-Z]+)', mode)
            for m in modes:
                for char in m[1]:
                    u = len(users) and users[0] or ''
                    if char == 'b':
                        if char[0] == '-':
                            self.getchannel(target).unban(u)
                        else:
                            self.getchannel(target).ban(u)
                        users.pop(0)
                    elif char == 'I':
                        if char[0] == '-':
                            self.getchannel(target).addinvite(u)
                        else:
                            self.getchannel(target).delinvite(u)
                        users.pop(0)
                    else:
                        c = self._modesymbol(char)
                        if c:
                            u = self.getchannel(target).getuser(u)
                            if not u:
                                raise Exception('Dude I dunno, what. Somebody snuck in somehow.')
                            if m[0] == '-':
                                u.delmode(c)
                            else:
                                u.addmode(c)
                            users.pop(0)
                        else:
                            self.getchannel(target).addmode(char)
        else:
            tar = target
            target = IRCUser(target)
        self.printline('{} sets mode {}: {} {}'.format(user.nick, tar, mode, ' '.join(userlist)))
        self.callhook('OnMode', user, target, mode, userlist)

    def OnNick(self, user, nick):
        user = IRCUser(user)
        for channel in self.channels.values():
            channel.rename(user, nick)
        self.printline('{} has changed names to {}.'.format(user.nick, nick))
        self.callhook('OnNick',user,nick)

    def OnNotice(self, user, target, message):
        user = IRCUser(user)
        if target[0] == '#':
            prefix = '{} -{}- '.format(target, user.nick)
        else:
            prefix = '-> -{}- '.format(user.nick)
            target = IRCUser(target)
        self.printline(prefix + message)
        self.callhook('OnNotice', user, target, message)

    def OnPart(self, user, channel):
        if not channel in self.channels:
            raise Exception("Channel didn't exist for some reason.")
        user = IRCUser(user)
        self.printline('{} has left {}.'.format(user.nick, channel))
        self.getchannel(channel).part(user)
        self.callhook('OnPart',user,channel)

    def OnPrivmsg(self, user, target, message):
        if target[0] == '#':
            chan = self.getchannel(target)
            if not chan:
                raise Exception("Couldn't find channel " + target)
            u = chan.getuser(user)
            if not u:
                self.printerr("Couldn't retrieve user {} from channel {}".format(user, target))
                user = IRCUser(user)
                self.send("who " + target)
            else:
                user = u
            prefix = '{} <{}{}> '.format(target, user.highestOp(), user.nick)
        else:
            user = IRCUser(user)
            target = IRCUser(target)
            prefix = '-> {}: '.format(user.nick)
        self.printline(prefix + message)
        self.callhook('OnPrivmsg', user, target, message)

    def OnQuit(self, user, message):
        user = IRCUser(user)
        for channel in self.channels.values():
            channel.part(user)
        self.printline('{} has quit ({})'.format(user.nick, message))
        self.callhook('OnQuit', user, message)

    def OnRaw(self, source, code, target, args):
        #I think raws will be the only exception. It won't convert to IRCUser before passing to callback.
        #It should be up to the hook callbacks to deal with nick!~user@hostmask and shit.
        self.callhook('OnRaw', source, code, target, args)

    def OnTopic(self, user, channel, topic):
        if not channel in self.channels:
            raise Exception("Channel didn't exist for some reason.")
        user = IRCUser(user)
        self.printline('{} changed topic to "{}"'.format(user.nick, topic))
        self.getchannel(channel).settopic(topic)
        self.callhook('OnTopic', user, channel, topic)

    def pausetimer(self, name):
        if not name in self.timers:
            return
        self.timers[name].stop()

    def printerr(self, err, *args):
        s = str(err)
        for arg in args:
            s += ' ' + str(arg)
        #I guess since print is in the func name, including \n is fine, as an analogue to print's behaviour
        self.err.write(s + '\n')

    def printline(self, msg):
        if self.print_callback:
            self.print_callback(msg + '\n')

    def reconnect(self):
        if self._reconnect_event.isSet():
            return
        self._reconnect_event.set()
        #this try/except probably doesn't belong here, buuut. y'know. can't think of where else it would go.
        #and it always helps to be polite to IRC servers.
        try:
            self.socket.send('QUIT\r\n')
        except:
            pass
        self.socket.close()
        self._connected = False
        #I wonder if reconnect event should stop all timers.
        #Let's say yes, for now.
        for k in self.timers.keys():
            self.timers[k].stop()
            self.timers.pop(k)
        self.connect()
        self._reconnect_event.clear()

    def run(self):
        readbuffer = ""
        while not self.stopped():
            if self._reconnect_event.isSet():
                continue
            try:
                readbuffer = readbuffer + self.socket.recv(1024)
            except socket.error as e:
                #if e.errno == 104:
                self.printerr('Socket error during read:', e)
                readbuffer = ""
                self.reconnect()
                #else:
                    #raise
            temp = readbuffer.split("\n")
            readbuffer = temp.pop()

            for line in temp:
                if self.stopped():
                    break
                line = line.rstrip()
                #if callback is passed, send raw line to it
                self.debug(line)
                words = line.split()
                words[0] = words[0].lstrip(':')
                if words[0] == "PING":
                    self.send("PONG " + words[1])
                elif words[1] == "JOIN":
                    self.OnJoin(words[0],words[2].lstrip(':'))
                elif words[1] == "KICK":
                    self.OnKick(words[0],words[2],words[3],self._join(words[4:]))
                elif words[1] == "MODE":
                    self.OnMode(words[0],words[2],words[3].lstrip(':'),words[4:])
                elif words[1] == "NICK":
                    self.OnNick(words[0],words[2].lstrip(':'))
                elif words[1] == "NOTICE":
                    self.OnNotice(words[0],words[2],self._join(words[3:]))
                elif words[1] == "PART":
                    self.OnPart(words[0],words[2])
                elif words[1] == "PRIVMSG":
                    self.OnPrivmsg(words[0],words[2],self._join(words[3:]))
                elif words[1] == "QUIT":
                    self.OnQuit(words[0],self._join(words[2:]))
                elif words[1] == "TOPIC":
                    self.OnTopic(words[0],words[2],self._join(words[3:]))
                else:
                    if words[1] == "001":
                        self.nick = words[2]
                        self.OnConnect()
                    elif words[1] == "332":
                        self.getchannel(words[3]).settopic(self._join(words[4:]))
                    elif words[1] == "333":
                        chan = self.getchannel(words[3])
                        if chan:
                            self.callhook('OnTopic', IRCUser(words[4]), words[3], chan.gettopic())
                    elif words[1] == "346":
                        self.getchannel(words[3]).addinvite(words[4])
                    elif words[1] == "352":
                        channel = self.getchannel(words[0])
                        #what the actual fuck why did this have to be added.
                        #why did this break.
                        if not channel:
                            continue
                        if not channel.getuser(words[4]):
                            user = IRCUser(words[4])
                            user.user = words[1]
                            user.host = words[2]
                            mode = words[5].lstrip('HG')
                            user.addmode(mode)
                            channel.join(user)
                    elif words[1] == "353":
                        channel = self.getchannel(words[4])
                        words[5] = words[5].lstrip(":")
                        for user in words[5:]:
                            channel.join(user)
                    elif words[1] == "367":
                        self.getchannel(words[3]).ban(words[4])
                    elif words[1] == "433":
                        if words[2] == '*':
                            if words[3] == "Bot-sama":
                                self.send("NICK Eden-sama")
                            elif words[3] == "Eden-sama":
                                self.send("NICK Bot-sama-san-chan-tan-kun")
                    self.OnRaw(words[0], words[1], words[2], words[3:])

    def send(self, text, override=False):
        try:
            self.socket.send(text + '\r\n')
        except socket.error as e:
            self.printerr('Socket error during send:', e)
            self.reconnect()

    def stop(self):
        self._stop.set()

    def stopped(self):
        return self._stop.isSet()

    def stoptimer(self, name):
        if not name in self.timers:
            return
        self.pausetimer(name)
        self.timers.pop(name)

    def timer(self, name, interval, callback, *args, **kwargs):
        #fail if timer name already exists. don't replace the timer, leave that up to the calling code.
        #nevermind, remove it.
        self.stoptimer(name)
        t = Timer(interval, callback, *args, **kwargs)
        self.timers[name] = t
        t.start()
        #return True, or return the timer itself?
        #or maybe just have it fail silently instead of failing with False.
        #let's go with True for now.
        return True

    def topic(self, chan, topic):
        self.send('TOPIC %s :%s' % (chan, topic))

    def user(self, user):
        return IRCUser(user)

class Timer(threading.Thread):
    #too lazy to change all tha numbers, so interval is in milliseconds.
    def __init__(self, interval, callback, limit=0, args=[], kwargs={}):
        threading.Thread.__init__(self)
        self.interval = interval / 1000.0
        self.callback = callback
        self.limit = limit
        self.args = args
        self.kwargs = kwargs
        self.iterations = 0
        self._stop = threading.Event()

    def restart(self):
        self.iterations = 0
        self._stop.clear()
        threading.Thread.__init__(self)
        self.start()

    def run(self):
        while not self._stop.wait(self.interval):
            self.callback(*self.args, **self.kwargs)
            self.iterations += 1
            if self.limit > 0 and self.iterations >= self.limit:
                break

    def stop(self):
        self._stop.set()

    def stopped(self):
        return self._stop.isSet()

#Gotta make it thread safe.
class LogWriter(object):
    def __init__(self, filename, timestamp=False):
        self.f = open(filename, 'a')
        self.lock = threading.Lock()
        self.timestamp = timestamp
        self._lastday = 0
        self.buffer = ''

    def write(self, msg):
        self.buffer += msg
        index = self.buffer.find('\n')
        while index != -1:
            msg = ''
            self.lock.acquire()
            if self._lastday != time.localtime()[2]:
                self._lastday = time.localtime()[2]
                msg += time.ctime() + '\n'
            if self.timestamp:
                msg += time.strftime('[%H:%M:%S] ')
            msg += self.buffer[:index+1]
            self.buffer = self.buffer[index+1:]
            if isinstance(msg, type(u'')):
                msg = msg.encode('utf-8', 'backslashreplace')
            self.f.write(msg)
            self.f.flush()
            self.lock.release()
            index = self.buffer.find('\n')

    def writeline(self, msg):
        self.write(msg.rstrip('\n') + '\n')

    def close(self):
        self.write('\n')
        self.f.close()

if __name__ == "__main__":
    err = LogWriter('/home/santiclause/ircbot/error.log', True)
    sys.stderr = err
    #out = LogWriter('/home/santiclause/ircbot/output.log', True)
    main = IRC('irc.rizon.net', 6667, 'Bot-sama', 'eden', 'Eden Radio', err=err)
    import hooks
    hooks.hook(main)
    main.connect()
    main.start()
